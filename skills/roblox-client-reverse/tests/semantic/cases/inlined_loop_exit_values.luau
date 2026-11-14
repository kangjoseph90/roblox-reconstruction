local function arrayLike(value)
    if type(value) ~= "table" then return false end
    for key in pairs(value) do
        if type(key) ~= "number" then return false end
    end
    return true
end
local function check(enabled, value)
    if enabled and value ~= nil and not arrayLike(value) then return "bad" end
    return "ok"
end
for _, value in ipairs({{}, {1, 2}, {named = 1}, false, "text"}) do
    print(check(true, value), check(false, value))
end
print(check(true, nil), check(false, nil))
