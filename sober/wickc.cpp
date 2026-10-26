// wickc: compile Luau source to the bytecode the Android Roblox engine runs (sober/wick_place.py uses it).
//
// Roblox clients only run bytecode with every opcode multiplied by 227 (mod 256), behind an 8-byte header
// (0xe009325b4a107a52, the same one the engine's own CoreScripts carry). A compile error becomes Luau's
// error blob, so the client reports it like a normal script error.
//
//   wickc LIST     LIST: lines "<in.luau>\t<out.bc>"
//
// Built by sober/wick_place.py against Luau 0.741 (~/code/ref/luau-0.741), the forked engine's release.
#include "Luau/BytecodeBuilder.h"
#include "Luau/BytecodeUtils.h"
#include "Luau/Compiler.h"

#include <cstdint>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>

struct RobloxEncoder : Luau::BytecodeEncoder
{
    void encode(uint32_t* data, size_t count) override
    {
        for (size_t i = 0; i < count;)
        {
            LuauOpcode op = LuauOpcode(uint8_t(data[i]));
            int len = Luau::getOpLength(op);
            data[i] = (data[i] & ~0xffu) | uint8_t(uint8_t(op) * 227);
            i += len;
        }
    }
};

int main(int argc, char** argv)
{
    if (argc != 2)
    {
        fprintf(stderr, "usage: wickc LIST\n");
        return 2;
    }
    std::ifstream list(argv[1]);
    std::string line;
    int n = 0, errors = 0;
    while (std::getline(list, line))
    {
        size_t tab = line.find('\t');
        if (tab == std::string::npos)
            continue;
        std::ifstream in(line.substr(0, tab), std::ios::binary);
        std::stringstream ss;
        ss << in.rdbuf();

        Luau::CompileOptions opts;
        opts.optimizationLevel = 1;
        opts.debugLevel = 1;
        opts.vectorLib = "Vector3";
        opts.vectorCtor = "new";
        opts.vectorType = "Vector3";
        RobloxEncoder enc;
        std::string bc = Luau::compile(ss.str(), opts, {}, &enc);
        if (!bc.empty() && bc[0] == 0)
        {
            errors++;
            fprintf(stderr, "wickc: %s:%s\n", line.substr(0, tab).c_str(), bc.c_str() + 1);
        }

        static const unsigned char magic[8] = {0x52, 0x7a, 0x10, 0x4a, 0x5b, 0x32, 0x09, 0xe0};
        std::ofstream out(line.substr(tab + 1), std::ios::binary);
        out.write((const char*)magic, 8);
        out.write(bc.data(), bc.size());
        n++;
    }
    fprintf(stderr, "wickc: %d compiled, %d with errors\n", n, errors);
    return 0;
}
