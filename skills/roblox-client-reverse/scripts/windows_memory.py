"""Windows read-only API and instance primitives reused from the live collector."""
import collections
import ctypes as C
from ctypes import wintypes as W
import json
import struct
from windows_access_probe import kernel, outcome

class MBI(C.Structure):
    _fields_ = [("BaseAddress", C.c_void_p), ("AllocationBase", C.c_void_p),
                ("AllocationProtect", W.DWORD), ("PartitionId", W.WORD),
                ("RegionSize", C.c_size_t), ("State", W.DWORD),
                ("Protect", W.DWORD), ("Type", W.DWORD)]

class Reader:
    def __init__(self, pid):
        self.k = kernel()
        self.k.VirtualQueryEx.argtypes = [W.HANDLE, C.c_void_p, C.POINTER(MBI), C.c_size_t]
        self.k.VirtualQueryEx.restype = C.c_size_t
        self.process = self.k.OpenProcess(0x410, False, pid)
        self.open_result = outcome(self.process)
        self.stats = collections.Counter()
        if not self.process:
            raise OSError(json.dumps(self.open_result))

    def read(self, address, size):
        if not 0x10000 <= address < 0x7fffffffffff or not 0 < size <= 32 * 1024**2:
            return b""
        buf, count = C.create_string_buffer(size), C.c_size_t()
        ok = self.k.ReadProcessMemory(self.process, address, buf, size, C.byref(count))
        error = 0 if ok else C.get_last_error()
        self.stats["read_calls"] += 1
        self.stats["bytes_read"] += count.value
        if not ok:
            self.stats["error_" + str(error)] += 1
        return buf.raw[:count.value]

    def regions(self):
        address = 0
        while address < 0x7fffffffffff:
            region = MBI()
            ok = self.k.VirtualQueryEx(self.process, address, C.byref(region), C.sizeof(region))
            if not ok:
                break
            base = region.BaseAddress or 0
            yield {"base": base, "size": region.RegionSize, "state": region.State,
                   "protect": region.Protect, "type": region.Type}
            following = base + region.RegionSize
            if following <= address:
                break
            address = following

    def close(self):
        self.k.CloseHandle(self.process)

def q(data, offset):
    return struct.unpack_from("<Q", data, offset)[0] if 0 <= offset <= len(data) - 8 else 0

def hits(data, needle, limit=256):
    pos = 0
    for _ in range(limit):
        pos = data.find(needle, pos)
        if pos < 0:
            break
        yield pos
        pos += 1

def string(reader, address, maximum=128):
    data = reader.read(address, 32)
    if len(data) != 32:
        return None
    size, capacity = q(data, 16), q(data, 24)
    if not 0 < size <= maximum or not size <= capacity <= max(8191, maximum):
        return None
    if capacity == 15:
        data = data[:size]
    elif capacity >= 16:
        data = reader.read(q(data, 0), size + 1)
        if len(data) != size + 1 or data[-1] != 0:
            return None
        data = data[:size]
    else:
        return None
    try:
        value = data.decode("utf-8")
    except UnicodeError:
        return None
    return value if all(ord(c) >= 32 for c in value) else None

class Inventory:
    def __init__(self, reader, descriptor_vtable, descriptor_offset, name_offset, parent_offset, self_offset=8):
        self.reader, self.descriptor_vtable = reader, descriptor_vtable
        self.descriptor_offset, self.name_offset, self.parent_offset = descriptor_offset, name_offset, parent_offset
        self.self_offset = self_offset
        self.header_size = max(128, max(descriptor_offset, name_offset, parent_offset, self_offset) + 8)
        self.cache, self.classes, self.names = {}, {}, {}

    def node(self, address):
        if address in self.cache:
            return self.cache[address]
        self.cache[address] = None
        data = self.reader.read(address, self.header_size)
        if len(data) != self.header_size or q(data, self.self_offset) != address:
            return None
        cd = q(data, self.descriptor_offset)
        if cd not in self.classes:
            descriptor = self.reader.read(cd, 32)
            self.classes[cd] = string(self.reader, q(descriptor, 8)) if q(descriptor, 0) == self.descriptor_vtable else None
        cls = self.classes[cd]
        name_ptr = q(data, self.name_offset)
        if name_ptr not in self.names:
            self.names[name_ptr] = string(self.reader, name_ptr)
        name = self.names[name_ptr]
        if not cls or name is None:
            return None
        result = {"address": address, "class": cls, "name": name,
                  "parent_address": q(data, self.parent_offset)}
        self.cache[address] = result
        return result

    def chain(self, address):
        result, seen = [], set()
        for _ in range(128):
            if not address:
                return result
            if address in seen:
                return None
            seen.add(address)
            node = self.node(address)
            if not node:
                return None
            result.append(node)
            address = node["parent_address"]
        return None
