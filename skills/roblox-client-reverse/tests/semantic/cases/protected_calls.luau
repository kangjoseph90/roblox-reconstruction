local function risky(x)
    if x == 0 then
        error("zero-case")
    end
    return "ok", x * 2
end

local ok, a, b = pcall(risky, 4)
print(ok, a, b)
ok, a = pcall(risky, 0)
print(ok, string.find(a, "zero-case") ~= nil)

local xok, xv = xpcall(function()
    return risky(0)
end, function(err)
    return "handled:" .. tostring(err):match("zero%-case")
end)
print(xok, xv)
