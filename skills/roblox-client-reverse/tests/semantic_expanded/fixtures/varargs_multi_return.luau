local function pack(...)
    return {n = select("#", ...), ...}
end
local function values(...)
    local a, b, c, d = ...
    return a + b, c, d, select("#", ...)
end
local function collect(...)
    local t = pack(...)
    local x, y, z, n = values(table.unpack(t, 1, t.n))
    return x, y, z, n, t.n
end
local x, y, z, n, count = collect(4, 8, "tail", nil, 23)
local single = (function() return 2, 99 end)()
print("OBS|multi=" .. tostring(x) .. "," .. tostring(y) .. "," .. tostring(z) .. "," .. tostring(n) .. "," .. tostring(count) .. "," .. tostring(single))
