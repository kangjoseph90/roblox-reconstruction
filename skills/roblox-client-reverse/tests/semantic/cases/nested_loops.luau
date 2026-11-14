local out = {}
for i = 1, 3 do
    local row = {}
    for j = 1, 4 do
        if j == 2 then
            continue
        end
        if i == 2 and j == 4 then
            break
        end
        table.insert(row, tostring(i) .. tostring(j))
    end
    table.insert(out, table.concat(row, ","))
end
print(table.concat(out, "/"))

local i = 0
local emitted = {}
while i < 3 do
    i += 1
    local j = 0
    while j < 3 do
        j += 1
        if j == 2 then
            break
        end
        table.insert(emitted, tostring(i) .. tostring(j))
    end
end
print(table.concat(emitted, " ") .. " end")
