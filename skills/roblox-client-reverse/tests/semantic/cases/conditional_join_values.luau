local function choose(first, second, input)
    local value = nil
    if first then
        if second then value = tostring(input) end
    end
    if value and #value > 0 then return value .. ":ok" end
    return "none"
end
for _, args in ipairs({{true, true, 7}, {true, false, 8}, {false, true, 9}, {true, true, ""}}) do
    print(choose(args[1], args[2], args[3]))
end
