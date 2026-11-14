local function classify(value)
    local category
    local adjusted
    if value < -1 then
        category = "very-low"
        adjusted = value + 10
    elseif value < 0 then
        category = "low"
        adjusted = value + 5
    elseif value == 0 then
        category = "zero"
        adjusted = 0
    elseif value < 3 then
        category = "small"
        adjusted = value * 2
    else
        category = "large"
        adjusted = value * 3
    end
    return category, adjusted, "shared-epilogue"
end

for _, value in ipairs({-4, -1, 0, 2, 5}) do
    print(classify(value))
end
