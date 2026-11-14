local a = function() return 1 end
local b = function() return 1 end
print(a == b, a == a)

local function left()
    return function() return 1 end
end
local function right()
    return function() return 1 end
end
local first = left()
local second = right()
print(first == second)
