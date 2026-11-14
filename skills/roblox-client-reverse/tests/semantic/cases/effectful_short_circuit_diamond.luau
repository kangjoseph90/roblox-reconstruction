local log = {}
local function mark(name, value)
    table.insert(log, name)
    return value
end

local function check(first, second)
    if not mark("left", first) or not mark("right", second) then
        mark("then", true)
    else
        mark("else", true)
    end
end

table.insert(log, "ff")
check(false, false)
table.insert(log, "ft")
check(false, true)
table.insert(log, "tf")
check(true, false)
table.insert(log, "tt")
check(true, true)
print(table.concat(log, ","))
