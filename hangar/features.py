"""Process-scoped research opt-ins. Ordinary releases show supported workflows."""
import os


def experimental_features():
    return {'fxr': os.environ.get('HANGAR_EXPERIMENTAL_FXR') == '1'}
