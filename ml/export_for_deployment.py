# =====================================================================
# EXPORT FOR DEPLOYMENT
# Paste this as the LAST cell of the Kaggle notebook and run it after
# all other cells (including the TorchXRayVision section).
# It writes /kaggle/working/tb_export.zip. Download it from the Output
# panel, unzip it, then either copy the files into backend/models/ (local)
# or upload them to Blob Storage and re-run CD (see README).
# =====================================================================
import json, shutil

EXPORT_DIR = "/kaggle/working/export"
shutil.rmtree(EXPORT_DIR, ignore_errors=True)
os.makedirs(EXPORT_DIR)

# 1) Inference-only copies of the 5 fold models.
#    - no augmentation layers (they only matter during training)
#    - float32 everywhere (training used mixed_float16 on the GPU, which is slow on a CPU server)
keras.mixed_precision.set_global_policy("float32")


def inference_only(src):
    backbone = CFG["fn"](include_top=False, weights=None, input_shape=(IMG_SIZE, IMG_SIZE, 3))
    backbone.set_weights(find_backbone(src).get_weights())
    inp = keras.Input((IMG_SIZE, IMG_SIZE, 3), name="image")          # raw 0-255 pixels, 3 channels
    x = layers.ReLU(max_value=255.0, name="clip")(inp)
    x = make_preprocess(CFG["prep"])(x)
    x = backbone(x, training=False)
    x = layers.GlobalAveragePooling2D(name="gap")(x)
    head = layers.Dense(1, activation="sigmoid", name="head")
    out = head(x)
    model = keras.Model(inp, out, name=f"tb_{BACKBONE}_inference")
    head.set_weights(src.get_layer("head").get_weights())
    return model


check = np.repeat(X[:4, ..., None], 3, axis=-1).astype("float32")
for k, src in enumerate(fold_models, 1):
    m = inference_only(src)
    diff = np.abs(m.predict(check, verbose=0) - src.predict(check, verbose=0)).max()
    print(f"fold {k}: max difference vs training model = {diff:.2e}")  # should be ~1e-3 or smaller
    m.save(f"{EXPORT_DIR}/inception_fold{k}.keras")

# 2) Chest X-ray check: reference features of the training X-rays + a distance threshold.
#    within-source: distance from each training image to its 5 nearest OTHER training images.
#    cross-source:  distance from each image to its 5 nearest images from the OTHER dataset. This
#                   simulates a chest X-ray from a hospital the model has never seen, so the
#                   threshold is not so tight that it rejects real X-rays from new sources.
#    The threshold is the larger of the two 99th percentiles.
GATE_K, GATE_PCT = 5, 99
ref = feats / np.linalg.norm(feats, axis=1, keepdims=True)
sims = ref @ ref.T
np.fill_diagonal(sims, -np.inf)                                   # ignore the image itself
within = 1 - np.sort(sims, axis=1)[:, -GATE_K:].mean(axis=1)
thr_within = float(np.percentile(within, GATE_PCT))

src = df["source"].to_numpy()
cross = [1 - np.sort(ref[src == s] @ ref[src != s].T, axis=1)[:, -GATE_K:].mean(axis=1)
         for s in np.unique(src)] if len(np.unique(src)) > 1 else []
thr_cross = float(np.percentile(np.concatenate(cross), GATE_PCT)) if cross else 0.0

knn_threshold = max(thr_within, thr_cross)
np.save(f"{EXPORT_DIR}/gate_ref_feats.npy", ref.astype(np.float16))  # ~3.5 MB
print(f"Gate kNN threshold = {knn_threshold:.4f}  "
      f"(within-source 99th pct {thr_within:.4f}, cross-source 99th pct {thr_cross:.4f})")

# 3) Threshold for the 5-fold average, picked on the out-of-fold predictions.
threshold = pick_threshold(y, oof_prob)
stats = summarize(y, oof_prob, threshold)
config = {
    "backbone": BACKBONE,
    "img_size": int(IMG_SIZE),
    "threshold": float(threshold),
    "target_sensitivity": TARGET_SENSITIVITY,
    "oof_auc": float(stats["auc"]),
    "oof_sensitivity": stats["sensitivity"],
    "oof_specificity": stats["specificity"],
    "gate": {"knn_k": GATE_K, "knn_threshold": knn_threshold},
    "keras_version": keras.__version__,
    "tensorflow_version": tf.__version__,
}
json.dump(config, open(f"{EXPORT_DIR}/config.json", "w"), indent=2)
print(json.dumps(config, indent=2))

shutil.make_archive("/kaggle/working/tb_export", "zip", EXPORT_DIR)
print("\nWrote /kaggle/working/tb_export.zip")
print("backend/pyproject.toml must use these or newer:",
      f"tensorflow-cpu=={tf.__version__}", f"keras=={keras.__version__}")
