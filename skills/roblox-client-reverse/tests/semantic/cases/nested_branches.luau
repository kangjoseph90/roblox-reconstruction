local function classify(a, b, c)
    if a then
        if b then
            return "ab"
        else
            if c then
                return "ac"
            else
                return "a"
            end
        end
    elseif b then
        return "b"
    else
        return "none"
    end
end

print(classify(true, true, false))
print(classify(true, false, true))
print(classify(true, false, false))
print(classify(false, true, false))
print(classify(false, false, true))
