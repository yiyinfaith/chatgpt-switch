"""Copy complete Windows icon resources without resizing or decoding pixels."""
import ctypes
import struct
from ctypes import wintypes


def assemble_icon(group, read_image):
    if len(group) < 6:
        raise ValueError('Incomplete icon group')
    reserved, kind, count = struct.unpack_from('<HHH', group)
    if reserved != 0 or kind != 1 or not 1 <= count <= 256 or len(group) != 6 + count * 14:
        raise ValueError('Invalid icon group')
    directory, images = [], []
    offset = 6 + count * 16
    for index in range(count):
        width, height, colors, zero, planes, bits, size, resource_id = struct.unpack_from('<BBBBHHIH', group, 6 + index * 14)
        data = read_image(resource_id)
        if not size or len(data) != size:
            raise ValueError('Incomplete icon image')
        directory.append(struct.pack('<BBBBHHII', width, height, colors, zero, planes, bits, size, offset))
        images.append(data)
        offset += size
    return struct.pack('<HHH', 0, 1, count) + b''.join(directory + images)


def executable_icon(path):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    kernel.LoadLibraryExW.restype = wintypes.HMODULE
    kernel.FreeLibrary.argtypes = [wintypes.HMODULE]
    kernel.FindResourceW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p]
    kernel.FindResourceW.restype = ctypes.c_void_p
    kernel.SizeofResource.argtypes = [wintypes.HMODULE, ctypes.c_void_p]
    kernel.SizeofResource.restype = wintypes.DWORD
    kernel.LoadResource.argtypes = [wintypes.HMODULE, ctypes.c_void_p]
    kernel.LoadResource.restype = ctypes.c_void_p
    kernel.LockResource.argtypes = [ctypes.c_void_p]
    kernel.LockResource.restype = ctypes.c_void_p
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wintypes.LPARAM)
    kernel.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, callback_type, wintypes.LPARAM]
    kernel.EnumResourceNamesW.restype = wintypes.BOOL
    # DATAFILE | IMAGE_RESOURCE: never execute code from the target application.
    library = kernel.LoadLibraryExW(str(path), None, 0x22)
    if not library:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        names = []
        @callback_type
        def collect(module, kind, name, context):
            names.append(name if name <= 0xffff else ctypes.wstring_at(name))
            return True
        if not kernel.EnumResourceNamesW(library, 14, collect, 0) or not names:
            raise OSError('Executable has no icon group')
        def resource(name, kind):
            buffer = ctypes.create_unicode_buffer(name) if isinstance(name, str) else None
            identifier = ctypes.cast(buffer, ctypes.c_void_p) if buffer is not None else name
            handle = kernel.FindResourceW(library, identifier, kind)
            if not handle:
                raise OSError('Missing icon resource')
            size = kernel.SizeofResource(library, handle)
            loaded = kernel.LoadResource(library, handle)
            data = kernel.LockResource(loaded) if loaded else None
            if not data or not size or size > 16 * 1024 * 1024:
                raise OSError('Invalid icon resource')
            return ctypes.string_at(data, size)
        name = 'IDR_MAINFRAME' if 'IDR_MAINFRAME' in names else names[0]
        return assemble_icon(resource(name, 14), lambda identifier: resource(identifier, 3))
    finally:
        kernel.FreeLibrary(library)


def notify_shortcut(path):
    shell = ctypes.WinDLL('shell32')
    shell.SHChangeNotify.argtypes = [wintypes.LONG, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p]
    shell.SHChangeNotify.restype = None
    buffer = ctypes.create_unicode_buffer(str(path))
    shell.SHChangeNotify(0x00002000, 0x0005, ctypes.cast(buffer, ctypes.c_void_p), None)
