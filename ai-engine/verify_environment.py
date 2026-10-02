import networkx
import numpy
import pandas
import sklearn


def main():
    print("Python environment verification passed.")
    print(f"pandas {pandas.__version__}")
    print(f"numpy {numpy.__version__}")
    print(f"scikit-learn {sklearn.__version__}")
    print(f"networkx {networkx.__version__}")


if __name__ == "__main__":
    main()
