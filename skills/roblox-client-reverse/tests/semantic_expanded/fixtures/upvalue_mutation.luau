local function make(seed)
    local value = seed
    local history = {}
    return function(delta)
        value += delta
        history[#history + 1] = value
        return value, table.concat(history, ":")
    end
end
local bump = make(7)
local a, ah = bump(2)
local b, bh = bump(-3)
local c, ch = bump(11)
print("OBS|upvalue=" .. tostring(a) .. "," .. ah .. ";" .. tostring(b) .. "," .. bh .. ";" .. tostring(c) .. "," .. ch)
