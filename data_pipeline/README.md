# Data Pipeline

Standalone scripts that produce the imputed sales dataset and the
Prophet predictions used to validate the model.

## Files in this folder

| File | Tracked in git? | What it is |
|------|-----------------|------------|
| `impute_products.py` | yes | Fills missing rows in the raw sales export. |
| `prophet_model_imputed.py` | yes | Trains Prophet on the imputed file and writes per-product predictions. |
| `product_sales_imputed_FINAL.xlsx` | **no** (~9 MB) | Output of `impute_products.py`. Keep on Drive / local disk. |
| `all_products_predictions_imputed.csv` | **no** (~11 MB) | Output of `prophet_model_imputed.py` (~195k rows). |

The two large files are deliberately excluded from git via
`.gitignore`. Generated outputs shouldn't be committed — they bloat
the repo and become stale the moment the input changes.

## Regenerating the outputs

```bash
cd data_pipeline
python impute_products.py            # → product_sales_imputed_FINAL.xlsx
python prophet_model_imputed.py      # → all_products_predictions_imputed.csv
```

Both scripts read the raw POS export from the path configured at the
top of each file. Update those paths if the data has moved.
