# SPDX-FileCopyrightText: 2026 ACBridge project contributors
# SPDX-License-Identifier: GPL-3.0-only
# T66 2026-10-26: licensing notice only; original executable code retained.
# Applies only to project-controlled contributions; see NOTICE and docs/licensing.md.
# Original TagTools/Havoc portions: Copyright (c) 2018 Skyth, MIT.
# Full original MIT notice in licenses/TagTools-MIT.txt; permission preserved.
# Local TCM0/compendium extensions: ACBridge contributions; see NOTICE.
"""TAG0 tagfile reader for Armored Core VI hkx files (TCM0 compendium support added).

T23-B extension: root "TCM0" (Havok type compendium shipped as *.compendium) is
supported: subsections TCID (u64 id list) + TYPE (same type reader).
Borrowing types from a compendium file (types_from=...) no longer requires the
borrowed file to have DATA/INDX sections.

Format facts and layout rules are ported to Python from the MIT-licensed
Havoc library (github.com/lingsamuel/TagTools, branch "havoc",
Copyright (c) 2018 Skyth), cross-checked byte-by-byte against AC6 files.
AC6 (SDKV20190100) is newer than the versions Havoc targets, so the SDK
version gate is only logged, never enforced.

Container (all sizes big-endian):
  section = {u32 flagsAndSize; char[4] sig; payload[Length]}
            Length = (flagsAndSize & 0x3FFFFFFF) - 8
            if (flagsAndSize & 0xC0000000): leaf, else payload = sub-sections
  TAG0 { SDKV(8-byte string), DATA(object blob), TYPE{...}, INDX{ITEM,PTCH,...}, TCRF? }
  TYPE subsections: TPTR, TST1|TSTR (class-name strings), TNA1|TNAM (type records),
                    FST1|FSTR (field-name strings), TBDY|TBOD (type bodies),
                    THSH (hashes), TPAD (padding)
  INDX/ITEM records (12 bytes): i32 typeIndex(&0xFFFFFF, 1-based, 0=null),
                                u32 position (relative to DATA payload start), i32 count
  Item positions inside DATA: flat struct arrays, element stride = type.ByteSize.

Usage:
  python tag0.py <file.hkx> [--types-from <other.hkx>] [--list-types] [--dump-root]
"""
import struct
import sys


class PackedReader:
    def __init__(self, data):
        self.d = data
        self.pos = 0

    def seek(self, pos):
        self.pos = pos

    def u8(self):
        v = self.d[self.pos]
        self.pos += 1
        return v

    def read(self, fmt, size):
        v = struct.unpack_from(fmt, self.d, self.pos)[0]
        self.pos += size
        return v

    def i32(self):
        return self.read("<i", 4)

    def u32(self):
        return self.read("<I", 4)

    def u64(self):
        return self.read("<Q", 8)

    def string(self, n):
        raw = self.d[self.pos : self.pos + n]
        self.pos += n
        return raw.split(b"\0")[0].decode("ascii", "replace")

    def cstring(self):
        end = self.d.index(b"\0", self.pos)
        s = self.d[self.pos : end].decode("ascii", "replace")
        self.pos = end + 1
        return s

    def packed(self):
        b0 = self.u8()
        if (b0 & 0x80) == 0:
            return b0
        top = b0 >> 3
        if 0x10 <= top <= 0x17:
            return ((b0 << 8) | self.u8()) & 0x3FFF
        if 0x18 <= top <= 0x1B:
            return ((b0 << 16) | (self.u8() << 8) | self.u8()) & 0x1FFFFF
        if top == 0x1C:
            return ((b0 << 24) | (self.u8() << 16) | (self.u8() << 8) | self.u8()) & 0x7FFFFFF
        if top == 0x1D:
            v = 0
            for i in range(4):
                v = (v << 8) | self.u8()
            return ((b0 << 32) | v) & 0x7FFFFFFFFFFFFFF
        if top == 0x1E:
            v = 0
            for i in range(6):
                v = (v << 8) | self.u8()
            return ((b0 << 48) | v) & 0x7FFFFFFFFFFFFFF
        if top == 0x1F:
            if (b0 & 7) == 0:
                v = 0
                for i in range(5):
                    v = (v << 8) | self.u8()
                return ((b0 << 40) | v) & 0xFFFFFFFFFF
            if (b0 & 7) == 1:
                v = 0
                for i in range(7):
                    v = (v << 8) | self.u8()
                return v
        return 0


class HkSection:
    def __init__(self, r: PackedReader):
        flags_and_size = struct.unpack_from(">I", r.d, r.pos)[0]
        r.pos += 4
        self.signature = r.string(4)
        self.position = r.pos
        self.length = (flags_and_size & 0x3FFFFFFF) - 8
        self.subsections = []
        if (flags_and_size & 0xC0000000) == 0:
            while r.pos < self.position + self.length:
                self.subsections.append(HkSection(r))
        else:
            r.pos = self.position + self.length
        self.raw_flags = flags_and_size

    def __repr__(self):
        return f"<Section {self.signature} @{self.position:#x} len={self.length:#x}>"


class HkType:
    FORMAT_NAMES = {
        0: "Void", 1: "Opaque", 2: "Bool", 3: "String",
        4: "Int", 5: "Float", 6: "Ptr", 7: "Class", 8: "Array",
    }
    HAS_FORMAT_INFO = 1 << 0
    HAS_SUBTYPE = 1 << 1
    HAS_VERSION = 1 << 2
    HAS_BYTESIZE = 1 << 3
    HAS_UNKNOWN_FLAGS = 1 << 4
    HAS_FIELDS = 1 << 5
    HAS_INTERFACES = 1 << 6

    def __init__(self):
        self.name = None
        self.parent = None
        self.flags = 0
        self.format_info = None
        self.sub_type = None
        self.version = None
        self.byte_size = None
        self.alignment = None
        self.unknown_flags = None
        self.fields = []
        self.interfaces = []
        self.parameters = []
        self.hash = 0

    # --- inherited properties, mirroring Havoc.Reflection.HkType ---
    @property
    def fmt(self):
        return (self.get_format_info() & 0xF)

    def get_format_info(self):
        if self.flags & self.HAS_FORMAT_INFO:
            return self.format_info or 0
        return self.parent.get_format_info() if self.parent else 0

    def get_sub_type(self):
        if self.flags & self.HAS_SUBTYPE:
            return self.sub_type
        return self.parent.get_sub_type() if self.parent else None

    def get_byte_size(self):
        if self.flags & self.HAS_BYTESIZE:
            return self.byte_size or 0
        return self.parent.get_byte_size() if self.parent else 0

    def get_fields(self):
        if self.flags & self.HAS_FIELDS:
            return self.fields
        return self.parent.get_fields() if self.parent else []

    def all_fields(self):
        if self.parent:
            return list(self.parent.all_fields()) + list(self.fields)
        return list(self.fields)

    @property
    def is_signed(self):
        return self.fmt in (2, 4) and (self.get_format_info() & 0x200) != 0

    @property
    def bit_count(self):
        return self.get_format_info() >> 10 if self.fmt in (2, 4) else 0

    @property
    def is_fixed_size(self):
        return self.fmt in (3, 8) and (self.get_format_info() & 0x20) != 0

    @property
    def fixed_size(self):
        if not self.is_fixed_size:
            return 0
        return self.get_format_info() >> 16 if self.fmt == 3 else self.get_format_info() >> 8

    @property
    def is_half(self):
        return self.fmt == 5 and self.get_format_info() >> 8 == 0x746

    @property
    def is_single(self):
        return self.fmt == 5 and self.get_format_info() >> 8 == 0x1746

    @property
    def is_double(self):
        return self.fmt == 5 and self.get_format_info() >> 8 == 0x345E

    def __repr__(self):
        return f"<HkType {self.name} fmt={self.FORMAT_NAMES.get(self.fmt)} size={self.get_byte_size()}>"

    def describe(self):
        fmt = self.FORMAT_NAMES.get(self.fmt, "?")
        extra = ""
        if self.fmt == 4:
            extra = f" {'i' if self.is_signed else 'u'}{self.bit_count}"
        if self.fmt == 3:
            extra = f" fixed={self.fixed_size}" if self.is_fixed_size else " ptr"
        if self.fmt == 8:
            st = self.get_sub_type()
            extra = f" <{st.name if st else '?'}> fixed={self.fixed_size}" if self.is_fixed_size else \
                    f" <{st.name if st else '?'}>"
        return f"{self.name} [{fmt}{extra}] size={self.get_byte_size()}"


class TagFile:
    def __init__(self, path, types_from=None):
        self.path = path
        with open(path, "rb") as source:
            self.data = source.read()
        self.types = []
        self.type_strings = []
        self.field_strings = []
        self.sdk = None
        self.tcrf_id = None
        self.data_offset = None
        self.items = []  # (type_or_None, position, count, flags)
        self.sections = []
        self.warnings = []
        self.is_compendium = False
        self.tcid_ids = []
        if types_from:
            other = TagFile(types_from)
            self.types = other.types
            self.type_strings = other.type_strings
            self.field_strings = other.field_strings
            self.warnings.append(f"types borrowed from {types_from}")
        self._parse()

    # ---------------- parsing ----------------

    def _parse(self):
        r = PackedReader(self.data)
        root = HkSection(r)
        if root.signature == "TCM0":
            self.is_compendium = True
            self.sections = root.subsections
            type_section = None
            for sec in root.subsections:
                if sec.signature == "TCID":
                    n = sec.length // 8
                    self.tcid_ids = [
                        struct.unpack_from("<Q", self.data, sec.position + i * 8)[0]
                        for i in range(n)]
                elif sec.signature == "TYPE" and not self.types:
                    type_section = sec
            if type_section is not None:
                self._read_type_section(type_section)
            return
        if root.signature != "TAG0":
            raise ValueError(f"not a TAG0/TCM0 file: {root.signature!r}")
        self.sections = root.subsections
        type_section = None
        indx_sections = []
        for sec in root.subsections:
            if sec.signature == "SDKV":
                self.sdk = self.data[sec.position : sec.position + 8].decode()
            elif sec.signature == "TCRF":
                self.tcrf_id = struct.unpack_from("<Q", self.data, sec.position)[0]
            elif sec.signature == "DATA":
                self.data_offset = sec.position
            elif sec.signature == "TYPE" and not self.types:
                type_section = sec
            elif sec.signature == "INDX":
                indx_sections.append(sec)
        if self.data_offset is None:
            raise ValueError("no DATA section")
        if type_section is not None:
            self._read_type_section(type_section)
        for sec in indx_sections:
            self._read_indx(sec)

    def _read_type_section(self, section):
        r = PackedReader(self.data)
        bodies = []
        for sub in section.subsections:
            r.seek(sub.position)
            sig = sub.signature
            if sig == "TPTR" or sig == "TPAD":
                continue
            if sig in ("TST1", "TSTR"):
                while r.pos < sub.position + sub.length:
                    self.type_strings.append(r.cstring())
            elif sig in ("FST1", "FSTR"):
                while r.pos < sub.position + sub.length:
                    self.field_strings.append(r.cstring())
            elif sig in ("TNA1", "TNAM"):
                self._read_type_names(r, sub)
            elif sig in ("TBDY", "TBOD"):
                bodies.append(sub)
            elif sig == "THSH":
                count = r.packed()
                for _ in range(count):
                    idx = r.packed()
                    if 0 < idx <= len(self.types):
                        self.types[idx - 1].hash = r.i32()
                    else:
                        r.pos += 4
            else:
                self.warnings.append(f"unknown TYPE sub-section {sig}")

        def read_type_index(idx=None):
            if idx is None:
                idx = r.packed()
            return None if idx == 0 else self.types[idx - 1]

        for sub in bodies:
            r.seek(sub.position)
            while r.pos < sub.position + sub.length:
                t = read_type_index()
                if t is None:
                    continue
                t.parent = read_type_index()
                t.flags = r.packed()
                if t.flags & HkType.HAS_FORMAT_INFO:
                    t.format_info = r.packed()
                if t.flags & HkType.HAS_SUBTYPE:
                    t.sub_type = read_type_index()
                if t.flags & HkType.HAS_VERSION:
                    t.version = r.packed()
                if t.flags & HkType.HAS_BYTESIZE:
                    t.byte_size = r.packed()
                    t.alignment = r.packed()
                if t.flags & HkType.HAS_UNKNOWN_FLAGS:
                    t.unknown_flags = r.packed()
                if t.flags & HkType.HAS_FIELDS:
                    b = r.u8()
                    if b >= 0x40:
                        self.warnings.append(f"fieldCount byte {b:#x} for {t.name}")
                    if b == 0xC3:
                        b = r.u8()
                        if b == 0:
                            b = r.packed()
                    field_count = b & 0x3F
                    for _ in range(field_count):
                        name_idx = r.packed()
                        flags = r.packed()
                        byte_offset = r.packed()
                        ftype_idx = r.packed()
                        ftype = read_type_index(ftype_idx)
                        t.fields.append({
                            "name": self.field_strings[name_idx],
                            "flags": flags,
                            "offset": byte_offset,
                            "type": ftype,
                        })
                if t.flags & HkType.HAS_INTERFACES:
                    n = r.packed()
                    for _ in range(n):
                        t.interfaces.append(
                            {"type": read_type_index(), "flags": r.packed()}
                        )

    def _read_type_names(self, r, sub):
        count = r.packed()
        self.types = [HkType() for _ in range(count)]
        for t in self.types:
            t.name = self.type_strings[r.packed()]
            template_count = r.packed()
            if template_count >= 0x40:
                template_count &= 0x3F
            for _ in range(template_count):
                pname = self.type_strings[r.packed()]
                if pname and pname[0] == "t":
                    pval = r.packed()  # type index
                    t.parameters.append((pname, pval))
                else:
                    t.parameters.append((pname, r.packed()))

    def _read_indx(self, section):
        for sub in section.subsections:
            if sub.signature != "ITEM":
                self.warnings.append(f"INDX sub-section {sub.signature} ignored")
                continue
            r = PackedReader(self.data)
            r.seek(sub.position)
            while r.pos + 12 <= sub.position + sub.length:
                type_index_raw = struct.unpack_from("<I", self.data, r.pos)[0]
                r.pos += 4
                position = r.u32()
                count = r.i32()
                if count < 0:
                    self.warnings.append(f"item count {count} at {r.pos:#x}")
                    count = 0
                flags = type_index_raw & 0xFF000000
                tidx = type_index_raw & 0xFFFFFF
                t = None
                if tidx:
                    if tidx - 1 < len(self.types):
                        t = self.types[tidx - 1]
                    else:
                        self.warnings.append(
                            f"item type index {tidx} out of range ({len(self.types)})")
                self.items.append((t, position, count, flags))

    # ---------------- object decoding ----------------

    def _abs(self, rel):
        return self.data_offset + rel

    def read_array_item(self, item_idx):
        """Return list of decoded objects for item `item_idx`."""
        if item_idx == 0:
            return []
        if item_idx >= len(self.items):
            raise ValueError(f"item index {item_idx} out of range")
        t, rel, count, _flags = self.items[item_idx]
        out = []
        if t is None:
            return out
        is_ptr_like = t.name == "hkStringPtr" or t.fmt == 6
        stride = 4 if is_ptr_like else max(t.get_byte_size(), 1)
        base = self._abs(rel)
        # H02: flat primitive arrays do not need reflected object dispatch per
        # byte. Keep list return type and all signed/float decoding semantics.
        fmt = t.fmt
        code = None
        if fmt == 4:
            code = {(8,False):'B',(8,True):'b',(16,False):'H',(16,True):'h',
                    (32,False):'I',(32,True):'i',(64,False):'Q',(64,True):'q'}.get((t.bit_count,t.is_signed))
        elif fmt == 5:
            code = 'e' if t.is_half else 'f' if t.is_single else 'd' if t.is_double else None
        if code and stride == struct.calcsize('<'+code):
            return list(struct.unpack_from('<'+str(count)+code,self.data,base))
        for i in range(count):
            out.append(self.read_object(t, base + i * stride, depth=0))
        return out

    def read_object(self, t: HkType, offset: int, depth: int = 0):
        if depth > 32:
            return "<depth-limit>"
        fmt = t.fmt
        d = self.data
        if fmt == 0:  # Void
            return None
        if fmt == 1:  # Opaque
            return f"<opaque {t.get_byte_size()}b>"
        if fmt == 2:  # Bool
            n = (t.bit_count // 8) or 1
            if n == 1:
                return d[offset] != 0
            if n == 2:
                return struct.unpack_from("<H", d, offset)[0] != 0
            if n == 4:
                return struct.unpack_from("<I", d, offset)[0] != 0
            return struct.unpack_from("<Q", d, offset)[0] != 0
        if fmt == 3:  # String
            if t.is_fixed_size:
                return d[offset : offset + t.fixed_size].split(b"\0")[0].decode("ascii", "replace")
            item_idx = struct.unpack_from("<i", d, offset)[0]
            if item_idx <= 0:
                return None
            chars = self.read_array_item(item_idx)
            return "".join(chr(c) if isinstance(c, int) else str(c) for c in chars[:-1])
        if fmt == 4:  # Int
            n = t.bit_count // 8
            if n == 1:
                v = d[offset]
                return v - 256 if (v > 127 and t.is_signed) else v
            if n == 2:
                return struct.unpack_from("<h", d, offset)[0] if t.is_signed else struct.unpack_from("<H", d, offset)[0]
            if n == 4:
                return struct.unpack_from("<i", d, offset)[0] if t.is_signed else struct.unpack_from("<I", d, offset)[0]
            if n == 8:
                return struct.unpack_from("<q", d, offset)[0] if t.is_signed else struct.unpack_from("<Q", d, offset)[0]
            raise ValueError(f"int bitcount {t.bit_count} for {t.name}")
        if fmt == 5:  # Float
            if t.is_half:
                return struct.unpack_from("<e", d, offset)[0]
            if t.is_single:
                return struct.unpack_from("<f", d, offset)[0]
            if t.is_double:
                return struct.unpack_from("<d", d, offset)[0]
            raise ValueError(f"float format info {t.get_format_info():#x} for {t.name}")
        if fmt == 6:  # Ptr
            item_idx = struct.unpack_from("<i", d, offset)[0]
            if item_idx <= 0:
                return None
            objects = self.read_array_item(item_idx)
            return objects[0] if objects else None
        if fmt == 7:  # Class
            out = {"__type__": t.name}
            for f in t.all_fields():
                ftype = f["type"]
                if ftype is None:
                    out[f["name"]] = None
                    continue
                out[f["name"]] = self.read_object(ftype, offset + f["offset"], depth + 1)
            return out
        if fmt == 8:  # Array
            if t.is_fixed_size:
                st = t.get_sub_type()
                stride = st.get_byte_size()
                return [self.read_object(st, offset + i * stride, depth + 1)
                        for i in range(t.fixed_size)]
            item_idx = struct.unpack_from("<i", d, offset)[0]
            return self.read_array_item(item_idx) if item_idx > 0 else []
        raise ValueError(f"unknown format {fmt} for {t.name}")

    def root_object(self):
        if len(self.items) < 2:
            return None
        t, rel, count, _ = self.items[1]
        return self.read_object(t, self._abs(rel))

    def find_type(self, name):
        for t in self.types:
            if t.name == name:
                return t
        return None


def main():
    path = sys.argv[1]
    types_from = None
    if "--types-from" in sys.argv:
        types_from = sys.argv[sys.argv.index("--types-from") + 1]
    tf = TagFile(path, types_from=types_from)
    print(f"file={path} ({len(tf.data)} bytes)")
    print(f"sdk={tf.sdk} tcrf={'0x%016x' % tf.tcrf_id if tf.tcrf_id else None}")
    if tf.is_compendium:
        print(f"compendium TCID: {['0x%016x' % v for v in tf.tcid_ids]}")
    print(f"sections={[s.signature for s in tf.sections]}")
    print(f"types={len(tf.types)} fields-strings={len(tf.field_strings)} items={len(tf.items)}")
    for w in tf.warnings[:10]:
        print(f"  warn: {w}")
    if "--list-types" in sys.argv:
        for i, t in enumerate(tf.types):
            print(f"  [{i+1:3d}] {t.describe()}")
    if "--dump-root" in sys.argv:
        root = tf.root_object()
        import json
        print(json.dumps(root, indent=1, default=str)[:6000])


if __name__ == "__main__":
    main()
