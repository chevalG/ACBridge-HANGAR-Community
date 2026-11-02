"""Bounded AC6 (64-bit little-endian TAE 0x1000D) motion references.

Layout follows the local SoulsFormatsNEXT TAE/Animation reader: Standard
imports HKX, ImportOtherAnim imports another TAE entry. Neither is a new HKX
or evidence that an in-game behavior is reconstructed.
"""
import hashlib
import struct
from pathlib import Path

def read_tae(path):
    raw=Path(path).read_bytes()
    if len(raw)>16*1024*1024 or raw[:8]!=b'TAE \x00\x00\x00\xff':
        raise ValueError('Unsupported TAE header; requires AC6 little-endian 64-bit')
    def read(fmt, offset):
        if offset<0 or offset+struct.calcsize(fmt)>len(raw):
            raise ValueError('TAE offset outside source')
        return struct.unpack_from(fmt,raw,offset)[0]
    if read('<I',8)!=0x1000D:
        raise ValueError('Unsupported TAE version')
    count, table=read('<i',0x54),read('<q',0x58)
    if not 0<=count<=8192 or table<=0 or table+count*16>len(raw):
        raise ValueError('TAE animation table exceeds bounds')
    rows={}
    for i in range(count):
        ident,body=read('<q',table+i*16),read('<q',table+i*16+8)
        if ident in rows: raise ValueError('Duplicate TAE animation ID')
        header=read('<q',body+24)
        kind=read('<I',header)
        data=read('<q',header+8)
        row={'id':ident,'header_type':kind,'target_id':ident,'reference':'direct',
             'events':read('<i',body+32)}
        if not 0<=row['events']<=65536: raise ValueError('TAE event count exceeds budget')
        if kind not in (0,1): raise ValueError('Unsupported TAE mini-header type')
        if data:
            if kind==0 and read('<B',data+9):
                row.update(target_id=read('<i',data+12),reference='imports_hkx')
            elif kind==1:
                row.update(target_id=read('<i',data+8),reference='imports_tae')
        elif kind==1:
            row.update(target_id=None,reference='null_import')
        rows[ident]=row
    for row in rows.values():
        seen=set(); target=row; chain=[]
        while target['reference']=='imports_tae':
            if target['id'] in seen:
                row['resolution_error']='TAE import cycle'; break
            seen.add(target['id']); chain.append(target['id'])
            target=rows.get(target['target_id'])
            if target is None:
                row['resolution_error']='Missing TAE import target'; break
        else:
            value=target['target_id']
            row['hkx_name']=f'a{value//1000000:03d}_{value%1000000:06d}' if value is not None and value>=0 else None
        row['import_chain']=chain
    return {'source':str(Path(path).resolve()),'sha256':hashlib.sha256(raw).hexdigest(),
            'rows':list(rows.values()),'scope':'motion references only; no behavior execution'}

def associate(clips, taes):
    for clip in clips:
        clip['tae_references']=[{'id':r['id'],'reference':r['reference'],'target_id':r['target_id'],
                                 'source':t['source'],'sha256':t['sha256']}
                                for t in taes for r in t['rows'] if r.get('hkx_name')==clip['name'].lower()]
