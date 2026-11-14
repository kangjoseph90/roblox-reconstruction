#include "cfg/CFG.h"
#include "emit/Emit.h"
#include <stdexcept>
#include <iostream>
#include <limits>
using namespace luaudec;

static void check(bool ok, const char* what) { if (!ok) throw std::runtime_error(what); }
static std::string render(const std::vector<StmtP>& stmts) { return emitBlock(stmts, 0); }
static CFG graph(std::initializer_list<std::vector<int>> edges)
{
    CFG cfg;
    for (const auto& succ : edges)
    {
        Block b; b.id = int(cfg.blocks.size());
        for (int to : succ) b.succs.push_back({to, Edge::Branch});
        cfg.blocks.push_back(b);
    }
    for (const Block& b : cfg.blocks)
        for (const Edge& e : b.succs) cfg.blocks[e.to].preds.push_back(b.id);
    analyzeCFG(cfg); return cfg;
}
static StmtP call()
{
    auto e = mkExpr(ExprKind::MethodCall); e->a = mkExpr(ExprKind::Vararg); e->str = "Method";
    auto s = mkStmt(StmtKind::Call); s->expr = e; return s;
}
static StmtP local(ExprP value)
{
    auto s = mkStmt(StmtKind::Local); s->names = {"x"}; s->values = {value}; return s;
}
int main()
{
    auto diamond = graph({{1,2},{3},{3},{}});
    check(diamond.postDominators[0][3] && !diamond.postDominators[0][1], "diamond merge");
    check(diamond.dominators[3][0] && !diamond.dominators[3][1], "diamond dominance");
    auto early = graph({{1,2},{},{}});
    check(early.postDominators[0][3] && !early.postDominators[0][1] && !early.postDominators[0][2], "synthetic return exit");
    auto loop = graph({{1},{2,3},{1},{}});
    check(loop.dominators[2][1] && loop.postDominators[1][3], "natural loop and exit");
    auto dead = graph({{1},{},{0}});
    check(!dead.reachable[2] && !dead.dominators[0][2], "unreachable back edge");
    auto forever = graph({{1},{1}});
    check(!forever.postDominators[0][1], "nonterminating component conservative postdom");
    auto mixed = graph({{1,2},{},{2}});
    check(!mixed.postDominators[0][1] && !mixed.postDominators[0][3], "mixed finite and infinite paths");
    check(render({call()}).find(';') == std::string::npos, "no separator at block head");
    check(render({local(mkNumber(1)),call()}).find(';') == std::string::npos, "literal needs no separator");
    auto name = mkName(ExprKind::Global,"f");
    check(render({local(name),call()}).find("f;\n") != std::string::npos, "identifier call ambiguity");
    auto comment = mkComment("retained");
    check(render({local(name),comment,call()}).find("f;\n-- retained\n") != std::string::npos, "separator precedes comments");
    check(render({call(),call()}).find("Method();\n") != std::string::npos, "call suffix ambiguity");
    auto fn = mkExpr(ExprKind::Function); fn->func = std::make_shared<FunctionBody>();
    check(render({local(fn),call()}).find(';') == std::string::npos, "function literal needs no separator");
    auto branch = mkStmt(StmtKind::If); branch->clauses.push_back({name,{call()}});
    check(render({branch}).find(';') == std::string::npos, "nested block first statement");
    check(render({local(mkNumber(std::numeric_limits<double>::infinity())),call()}).find("math.huge;\n") != std::string::npos, "infinity spelling is a prefix expression");
    check(render({local(mkUnOp("-",mkNumber(-1))),call()}).find("-(-1);\n") != std::string::npos, "generated unary parentheses accept a suffix");
    std::cout << "native CFG/emitter invariants: 15 passed\n";
}
