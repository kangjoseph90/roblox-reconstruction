local function makeCounter(start)
    local value = start
    return function(delta)
        value += delta
        return value
    end
end

local left = makeCounter(10)
local right = makeCounter(100)
print(left(1), left(4), right(-3), left(0), right(2))

local funcs = {}
for i = 1, 3 do
    local captured = i * 10
    funcs[i] = function(extra)
        return captured + extra
    end
end
print(funcs[1](1), funcs[2](2), funcs[3](3))
