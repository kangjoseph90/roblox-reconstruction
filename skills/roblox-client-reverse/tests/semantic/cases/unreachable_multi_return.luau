local function choose(n)
    if n == 1 then
        return "one", 1
    elseif n == 2 then
        return "two", 2
    end
    return "other", n
end

if false then
    print("UNREACHABLE-BRANCH")
end

local a, b = choose(1)
print(a, b)
a, b = choose(2)
print(a, b)
a, b = choose(7)
print(a, b)
