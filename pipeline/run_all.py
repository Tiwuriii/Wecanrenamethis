"""Rebuild every map layer: fetch sources, compute heat, run the analysis."""
import build
import context
import fetch
import heat

if __name__ == "__main__":
    print("1/4 fetch")
    fetch.main()
    print("2/4 heat")
    heat.main()
    print("3/4 build")
    build.main()
    print("4/4 context")
    context.main()
