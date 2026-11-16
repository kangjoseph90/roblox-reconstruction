-- Authored regression: a branch may overwrite a value without defining it on every path.
local function mark(value)
    local prefix = ""
    if value < 0 then
        prefix = "minus:"
    end
    return prefix .. tostring(math.abs(value))
end

local function display(value, state)
    local visible = true
    if value == nil then
        visible = false
        state.text = ""
    else
        state.text = value
    end
    state.visible = visible
end

local function count(flag)
    local total = 4
    if flag then total = 9 end
    return total + 1
end

print(mark(0), mark(7), mark(-7))
local state = {}
display("shown", state)
print(state.text, state.visible)
display(nil, state)
print("[" .. state.text .. "]", state.visible)
print(count(false), count(true))
