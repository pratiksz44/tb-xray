# ml/: training and export

| File | Purpose |
|---|---|
| `tb_detection_training.ipynb` | Kaggle training notebook (5-fold InceptionV3 + TorchXRayVision features) |
| `export_for_deployment.py` | Last notebook cell: writes `tb_export.zip` |

`tb_export.zip` contains everything the backend loads:

```
config.json                  img_size, decision threshold, chest X-ray check threshold, OOF metrics
gate_ref_feats.npy           TorchXRayVision features of the training X-rays (chest X-ray check)
inception_fold{1..5}.keras   the 5 fold models
```

To release a new model, upload the unzipped files to Blob Storage and re-run the **CD** workflow (see the
root README).
