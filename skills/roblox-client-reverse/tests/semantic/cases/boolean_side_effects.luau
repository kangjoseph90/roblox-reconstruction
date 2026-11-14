local log = {}
local function mark(name, value)
    table.insert(log, name)
    return value
end

local a = mark("a", false) and mark("b", true) or mark("c", 4)
local b = mark("d", true) or mark("e", true) and mark("f", false)
local c = not mark("g", nil) and (mark("h", 0) or mark("i", "fallback"))
local d = (mark("j", false) or mark("k", "kept")) and mark("l", true)
print(tostring(a), tostring(b), tostring(c), tostring(d))
print(table.concat(log, ","))
