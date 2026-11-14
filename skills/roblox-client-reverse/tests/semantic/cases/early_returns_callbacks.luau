local trace = {}
local function finish(x)
    if x < 0 then
        return "negative", x
    end
    if x == 0 then
        return "zero", 0
    end
    return "positive", x
end

local function callback(flag)
    local answer
    local cb = function()
        if flag then
            return "callback-return"
        end
        answer = "continued"
    end
    local result = cb()
    if result then
        table.insert(trace, result)
    else
        table.insert(trace, answer)
    end
    return result or answer
end

local a, b = finish(-2)
print(a, b)
a, b = finish(0)
print(a, b)
a, b = finish(3)
print(a, b)
print(callback(true))
print(callback(false))
print(table.concat(trace, ","))
