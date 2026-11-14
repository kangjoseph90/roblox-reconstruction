local function calculate(x, y)
    local scaled = y / x
    return scaled * scaled + y * y
end
for _, pair in ipairs({{2, 3}, {4, 8}, {-2, 6}}) do
    print(calculate(pair[1], pair[2]))
end
