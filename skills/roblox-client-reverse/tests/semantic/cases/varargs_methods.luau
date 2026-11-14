local function pack(...)
    return table.pack(...)
end
local function relay(prefix, ...)
    local values = pack(...)
    return prefix, values.n, values[1], values[2], values[3]
end

local object = {base = 7}
function object:add(a, b)
    self.base += a
    return self.base, b or "default"
end

local x, y, z, q, r = relay("v", 3, nil, 9)
print(x, y, z, tostring(q), r)
local m, n = object:add(5, "method")
print(m, n, object.base)
