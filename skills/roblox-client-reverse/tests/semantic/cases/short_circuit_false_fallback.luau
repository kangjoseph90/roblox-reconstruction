-- Authored reproduction of value-carrying fallback seen in captured modules.
local function dragging(self)
    return (self.handle and self.handle:isDragging()) or false
end
print(type(dragging({})), dragging({}))
for _, item in ipairs({{}, {false}, {true}, {7}}) do
    local calls = 0
    local self = {handle = {isDragging = function()
        calls += 1
        return item[1]
    end}}
    local result = dragging(self)
    print(type(result), result, calls)
end
