local function factory()
    return function()
        print("called")
    end
end

do
    local f = factory()
end;
(function()
    print("block-first-call")
end)()

-- Comment between a block and a parenthesized call must not attach it to the block.
do
    print("block-first")
end
-- next expression is parenthesized and starts a new statement
;(function()
    print("expression-first")
end)()

local obj = {value = 6}
do
    obj.value += 1
end
;(function()
    print("after-assignment", obj.value)
end)()
