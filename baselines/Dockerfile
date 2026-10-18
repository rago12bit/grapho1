# RCAEval's own implementations of the published baselines, in their own image so none of their dependencies
# reach Hypocenter's. Only the metric-based methods that need no torch / Python 3.8 environment.
#   docker build -t faultline-baselines baselines
#   docker run --rm -e OMP_NUM_THREADS=1 -e OPENBLAS_NUM_THREADS=1 -v "$PWD/data:/data" -v "$PWD/baselines:/b:ro" faultline-baselines python /b/run_baselines.py
FROM python:3.12-slim
RUN pip install --no-cache-dir 'RCAEval==1.8.0' 'numpy<2' 'pandas==2.2.3' 'pyarrow==17.0.0' scikit-learn scipy 'statsmodels>=0.14.2' \
    'networkx==2.5' requests tqdm pyyaml joblib causal-learn pingouin
