local function f(x)
 local y = if x then true else false
 return type(y), y
end
local function g(x)
 local y = (x and true) or false
 return type(y), y
end
for _, a in ipairs({{}, {false}, {true}, {0}, {7}, {""}, {"yes"}}) do print(f(a[1])); print(g(a[1])) end
