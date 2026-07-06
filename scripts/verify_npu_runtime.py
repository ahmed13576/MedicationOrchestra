import sys

def main():
    try:
        import onnxruntime as ort
        print(f"ONNX Runtime version: {ort.__version__}")
        print("Available Execution Providers:")
        for ep in ort.get_available_providers():
            print(f" - {ep}")
    except ImportError:
        print("onnxruntime is not installed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
