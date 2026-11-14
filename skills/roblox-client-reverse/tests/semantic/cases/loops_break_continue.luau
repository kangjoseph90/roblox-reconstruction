local sum = 0
for i = 1, 10 do
    if i == 2 or i == 5 then
        continue
    end
    if i == 8 then
        break
    end
    sum += i
end
print(sum)

local j = 0
local emitted = {}
while j < 6 do
    j += 1
    if j == 3 then
        continue
    end
    if j == 5 then
        break
    end
    table.insert(emitted, tostring(j))
end
print(table.concat(emitted, ":") .. ":done")
