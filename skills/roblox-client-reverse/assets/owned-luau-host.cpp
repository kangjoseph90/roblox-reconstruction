#include "Luau/Compiler.h"

#include "lua.h"
#include "lualib.h"

#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

namespace
{
std::string readFile(const char* path)
{
    std::ifstream input(path, std::ios::binary);
    if (!input)
        throw std::runtime_error(std::string("cannot open source: ") + path);

    return std::string(std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
}

void writeFile(const char* path, const std::string& bytes)
{
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output)
        throw std::runtime_error(std::string("cannot open capture path: ") + path);

    output.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    if (!output)
        throw std::runtime_error(std::string("failed writing capture path: ") + path);
}
} // namespace

int main(int argc, char** argv)
{
    if (argc != 3)
    {
        std::cerr << "usage: luau-host <owned-source.luau> <capture.luac>\n";
        return 2;
    }

    lua_State* state = nullptr;
    try
    {
        const std::string source = readFile(argv[1]);
        Luau::CompileOptions options;
        options.optimizationLevel = 1;
        options.debugLevel = 2;
        options.typeInfoLevel = 1;

        // This is an upstream Luau compiler API call over the supplied local source.
        const std::string bytecode = Luau::compile(source, options);
        if (bytecode.size() < 2)
            throw std::runtime_error("compiler returned a truncated bytecode buffer");

        // Capture the exact in-memory buffer immediately before the luau_load boundary.
        writeFile(argv[2], bytecode);
        std::cerr << "source=" << argv[1] << '\n'
                  << "capture=" << argv[2] << '\n'
                  << "capture_length=" << bytecode.size() << '\n'
                  << "bytecode_version=" << static_cast<unsigned>(static_cast<unsigned char>(bytecode[0])) << '\n'
                  << "types_version=" << static_cast<unsigned>(static_cast<unsigned char>(bytecode[1])) << '\n'
                  << "capture_boundary=immediately-before-luau_load\n";

        state = luaL_newstate();
        if (!state)
            throw std::runtime_error("luaL_newstate failed");

        luaL_openlibs(state);
        luaL_sandbox(state);

        if (luau_load(state, argv[1], bytecode.data(), bytecode.size(), 0) != 0)
        {
            const char* error = lua_tostring(state, -1);
            throw std::runtime_error(std::string("luau_load failed: ") + (error ? error : "unknown error"));
        }

        if (lua_pcall(state, 0, LUA_MULTRET, 0) != 0)
        {
            const char* error = lua_tostring(state, -1);
            throw std::runtime_error(std::string("script execution failed: ") + (error ? error : "unknown error"));
        }

        lua_close(state);
        return 0;
    }
    catch (const std::exception& error)
    {
        if (state)
            lua_close(state);
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
