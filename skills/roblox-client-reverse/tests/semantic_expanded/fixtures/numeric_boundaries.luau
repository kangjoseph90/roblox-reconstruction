local x = 9007199254740991
local y = x + 5
local z = 1 / 10
local inf = 1 / 0
local nan = 0 / 0
local function class(v)
    if v ~= v then return "nan" end
    if v == math.huge then return "inf" end
    if v == -math.huge then return "-inf" end
    return string.format("%.17g", v)
end
local exact = (2 ^ 53) == (2 ^ 53 + 1)
print("OBS|numeric=" .. class(x) .. "," .. class(y) .. "," .. class(z) .. "," .. class(inf) .. "," .. class(nan) .. "," .. tostring(exact))
print("OBS|arith=" .. class((y * 17 - x * 17) / 17) .. "," .. class(math.floor(-7 / 3)) .. "," .. class(-7 % 3))
