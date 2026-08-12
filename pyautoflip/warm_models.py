from insightface.app import FaceAnalysis


for name in ("buffalo_s", "buffalo_l"):
    app = FaceAnalysis(name=name, providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))
    print(f"insightface {name} ready")
