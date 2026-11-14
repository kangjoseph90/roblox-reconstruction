local shared = {count = 1, nested = {value = 10}}
local alias = shared
local events = {}
local function mutate(t, delta)
    t.count += delta
    t.nested.value *= delta
    events[#events + 1] = tostring(t.count) .. ":" .. tostring(t.nested.value)
end
mutate(alias, 3)
mutate(shared, -2)
print("OBS|same=" .. tostring(shared == alias) .. "|" .. tostring(shared.count) .. "|" .. tostring(shared.nested.value))
print("OBS|events=" .. table.concat(events, ","))
