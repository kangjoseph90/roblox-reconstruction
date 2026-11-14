local function firstInBlock(x, ...)
    if x then (...):Method(x) end
end

local function afterAssignment(x, ...)
    local value = tostring(x);
    (...):Method(value)
    return value
end

local receiver = {}
function receiver:Method(value)
    print("method", tostring(value))
end

for _, flag in ipairs({false, true}) do
    firstInBlock(flag, receiver)
end
afterAssignment(42, receiver)
