local config = {
    modes = {{name = "cold", flags = {true, false, true}}, {name = "hot", flags = {false, true}}},
    index = {primary = {scale = 1.25, offset = -3}, backup = {scale = 0.5, offset = 9}},
    history = {},
}
local function resolve(key, fallback)
    local entry = config.index[key] or config.index[fallback]
    return entry.scale, entry.offset
end
for i, mode in ipairs(config.modes) do
    local scale, offset = resolve(i == 1 and "primary" or "missing", "backup")
    local enabled = 0
    for _, flag in ipairs(mode.flags) do if flag then enabled += 1 end end
    config.history[#config.history + 1] = mode.name .. ":" .. tostring(scale * enabled + offset)
end
config.index.primary.scale += config.index.backup.scale
print("OBS|init=" .. table.concat(config.history, ",") .. "|" .. tostring(config.index.primary.scale))
