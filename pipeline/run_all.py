"""Rebuild every map layer: fetch sources, compute heat, run the analysis."""
import build
import fetch
import heat

if __name__ == "__main__":
    print("1/3 fetch")
    fetch.main()
    print("2/3 heat")
    heat.main()
    print("3/3 build")
    build.main()
