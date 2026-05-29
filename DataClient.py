# -*- coding: utf-8 -*-
"""
Created on Fri Feb 13 10:32:53 2026

@author: A63837
"""

import boto3
from botocore.config import Config
from pathlib import Path
from dotenv import load_dotenv
import os
import warnings
import json
import hashlib
from datetime import datetime, timezone
import pandas as pd
import pyarrow, polars as pl
import sys
import logging 
import io
import oracledb
import getpass
from pyaim import CCPPasswordREST
import pickle
    
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)

warnings.filterwarnings('ignore')

class DataClient:
    
    def __init__(self):
        
        self.APP_ENV = os.getenv("RUNNING_ENV","LOCAL")
        self.load_environment(self.APP_ENV)
        self.ora_pool = None
        
        ACCESS_KEY = os.environ["AWS_ACCESS_KEY_ID"]
        SECRET_KEY = os.environ["AWS_SECRET_ACCESS_KEY"]
        self.BUCKET_NAME = os.environ["AWS_S3_BUCKET"]
        ENDPOINT = os.environ["AWS_S3_ENDPOINT"]
        
        session = boto3.Session(
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY
        )
        
        
        self.s3 = session.client('s3',
                            endpoint_url=ENDPOINT,
                            verify=True,
                            config=Config(s3={"addressing_style":"path"}))
    
    def load_environment(self, APP_ENV):
        env_path = None
    
        if APP_ENV == "LOCAL":
            env_path = Path.home() / ".env-s3"
            load_dotenv(env_path, override=False)
            
        if env_path and env_path.exists():
            print(f"[ENV] Loaded from: {env_path}")
        else:
            print(f"[ENV] Using system environment variables (APP_ENV={APP_ENV})")

    def get_bucket_content(self):
        
        try:
            response = self.s3.list_objects_v2(Bucket=self.BUCKET_NAME)
            
            if 'Contents' in response:
                print(f"Files in {self.BUCKET_NAME}:")
                for obj in response['Contents']:
                    print(f" - {obj['Key']} (Size: {obj['Size']} bytes)")
            else:
                print("The bucket is empty.")
                
        except Exception as e:
            print(f"Error connecting: {e}")
            
    def get_bucket_usage(self, prefix: str | None = None) -> dict:    
        total_size_bytes = 0
        object_count = 0
    
        paginator = self.s3.get_paginator("list_objects_v2")
    
        paginate_params = {"Bucket": self.BUCKET_NAME}
        if prefix:
            paginate_params["Prefix"] = prefix
    
        for page in paginator.paginate(**paginate_params):
            if "Contents" not in page:
                continue
    
            for obj in page["Contents"]:
                total_size_bytes += obj["Size"]
                object_count += 1
    
        total_size_gb = total_size_bytes / (1024 ** 3)
    
        return {
            "total_size_bytes": total_size_bytes,
            "total_size_gb": round(total_size_gb, 4),
            "object_count": object_count,
        }           
    def _sha256_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _safe_ts_for_path(self, ts: str) -> str:
        return ts.replace(":", "").replace("-", "")
        
    def _sha256_bytes(self, b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()
    
    def _utc_ts(self) -> str:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
    
    def _upload_bytes(self, key: str, data: bytes, content_type: str | None = None,
                      *, if_none_match: bool = False):
        kwargs = {"Bucket": self.BUCKET_NAME, "Key": key, "Body": data}
        if content_type:
            kwargs["ContentType"] = content_type
        if if_none_match:
            # Conditional create: the PUT fails with 412 PreconditionFailed if
            # the key already exists. Lets callers (the block store) make a
            # versioned write atomic instead of a racy check-then-write.
            kwargs["IfNoneMatch"] = "*"
        self.s3.put_object(**kwargs)
    
    def _head(self, key: str) -> dict:
        return self.s3.head_object(Bucket=self.BUCKET_NAME, Key=key) 
    
    def delete_file(self, key: str):
        self.s3.delete_object(Bucket=self.BUCKET_NAME, Key=key)
        print(f"Deleted: {key}")
    
    def delete_prefix(self, prefix: str):
        paginator = self.s3.get_paginator("list_objects_v2")
    
        to_delete = []
        for page in paginator.paginate(Bucket=self.BUCKET_NAME, Prefix=prefix):
            for obj in page.get("Contents", []):
                to_delete.append({"Key": obj["Key"]})
    
        if not to_delete:
            print("Nothing found.")
            return
    
        # S3 supports batch delete (1000 at once)
        for i in range(0, len(to_delete), 1000):
            batch = to_delete[i:i+1000]
            self.s3.delete_objects(
                Bucket=self.BUCKET_NAME,
                Delete={"Objects": batch}
            )
    
        print(f"Deleted {len(to_delete)} objects under {prefix}")
    
    def cleanup_old_runs(self, dataset: str, base_prefix: str = "sdp"):
        """
        Deletes all run_id folders except the latest one.
        """
        
        latest_key = f"{base_prefix}/{dataset}/LATEST.json"
        latest = self.get_json(latest_key)
        latest_run_id = latest["run_id"]
    
        prefix = f"{base_prefix}/{dataset}/"
        files = self.list_prefix(prefix)
    
        run_prefixes = set()
        for f in files:
            if "run_id=" in f:
                run = f.split("run_id=")[1].split("/")[0]
                run_prefixes.add(run)
    
        deleted = 0
    
        for run in run_prefixes:
            if run != latest_run_id:
                del_prefix = f"{base_prefix}/{dataset}/run_id={run}/"
                self.delete_prefix(del_prefix)
                deleted += 1
    
        print(f"Cleanup finished. Deleted {deleted} old runs. Latest kept: {latest_run_id}")

    def _connection_user_info(self, use_local_info=False) -> tuple:
        result = ("", "")    
        username = getpass.getuser()     
        aimccp = CCPPasswordREST('https://seip-vip-prd-aam.qnbfinansbank.com', verify=True) # set verify=False to ignore SSL
        service_status = aimccp.check_service()
        
        if use_local_info:
            with open("C:/Toad/get_password.txt", "r") as f:
                username = os.environ["USERNAME"]
                password = f.read()
            result = (username, password)
        else:    
            if service_status in ['SUCCESS: AIMWebService Found. Status Code: 200', 'Configuration validated for seip-vip-prd-aam.qnbfinansbank.com/AIMWebService. Use GetPassword() to verify service health.']:
                response = aimccp.GetPassword(appid=f'{username}AppId',
                                              safe=f'USER_Python_{username}',
                                              object=f'Database-User-Python-Oracle-OneTime-seip-dbora-prd-edw-edw2-{username}PY',
                                              reason='Python DB Connection')
                username = response['UserName']
                password = response['Content']
                result = (username, password)
            else:
                raise Exception(service_status)    
        
        return result
    
    def get_connection(self, use_local_info: bool = False) -> oracledb.Connection:
        if self.APP_ENV == "LOCAL":
            username, password = self._connection_user_info(use_local_info)
            
            dsn_tns = oracledb.makedsn("ddm_edw.finansbank.com.tr", 9522, service_name="enduser")
            conn = oracledb.connect(user=username, password=password, dsn=dsn_tns)
        else:
            username = os.environ.get('DB_USERNAME')
            password = os.environ.get('DB_PASSWORD')

            dsn_tns = oracledb.makedsn("ddm_edw.finansbank.com.tr",9522,service_name = "enduser")
            conn = oracledb.connect(user=username, password=password, dsn=dsn_tns)
            
        return conn
    
    def drop_connection(self, con: oracledb.Connection):
        if con:
            con.close()
    
    def create_connection_pool(self, use_local_info: bool = False, con_min_count: int = 1, con_max_count: int = 10):
        username, password = self._connection_user_info(use_local_info)
        dsn_tns = oracledb.makedsn("ddm_edw.finansbank.com.tr", 9522, service_name="enduser")
        self.ora_pool = oracledb.create_pool(user=username, password=password, dsn=dsn_tns, min=con_min_count, max=con_max_count, increment=1)                
        
    def close_connection_pool(self):
        self.ora_pool.close(force=True)
        self.ora_pool = None
    
    def get_connection_from_pool(self) -> oracledb.Connection:
        if self.ora_pool == None:
            self.create_connection_pool()
        
        result = self.ora_pool.acquire()
        
        return result
    
    def drop_connection_from_pool(self, con: oracledb.Connection):
        self.ora_pool.release(con)
        
    def edw_query_to_polars(self, con: oracledb.Connection, query: str, params: dict = {}) -> pl.DataFrame:
        result = None
        odf = con.fetch_df_all(statement=query, parameters=params, arraysize=5_000_000)
        result = pl.from_arrow(odf)
        return result
    
    def edw_query_to_pandas(self, con: oracledb.Connection, query: str, params: dict = {}) -> pd.DataFrame:
        result = None
        odf = con.fetch_df_all(statement=query, parameters=params, arraysize=5_000_000)
        result = pyarrow.table(odf).to_pandas()
        return result
        
    def edw_write_df_to_table(self, con: oracledb.Connection, table: str, df: pd.DataFrame | pl.DataFrame):
        cursor = con.cursor()
        columns = df.columns
        sql_columns = ",".join(columns)
        sql_binding_locs = ",".join([":" + c for c in columns])
        query = f"INSERT INTO {table} ({sql_columns}) VALUES ({sql_binding_locs})"
        
        cursor.executemany(query, df)
        con.commit()
        cursor.close()

    def edw_create_ddl_from_df(self, table_to_create: str, src_df: pl.DataFrame | pd.DataFrame) -> str:
        if isinstance(src_df, pd.DataFrame):
            df = pl.from_pandas(src_df)
        else:
            df = src_df
            
        str_lengths = ( 
            df.select(pl.col(pl.String()).str.len_bytes().max())
            .unpivot()
            .with_columns(pl.col("value").fill_null(32))
            .to_dict(as_series=False)
        )
        str_lengths_dict = {str_lengths["variable"][v]: str_lengths["value"][v] 
                            for v, val in enumerate(str_lengths["variable"])}
        create_table_body = []
        
        for col_nm, col_type in df.schema.items():
            str_col_type = ""
            if col_type.is_numeric():
                str_col_type = "NUMBER"
            elif col_type.is_temporal():
                str_col_type = "DATE"
            elif col_type.is_(pl.String):
                str_col_type = f"VARCHAR2({2 * str_lengths_dict[col_nm]})"            
            else:
                str_col_type = "NUMBER"
            create_table_body.append(f"{col_nm} {str_col_type}")
    
        result = f"CREATE TABLE {table_to_create} (" + ",".join(create_table_body) + ")"
        
        return result
    
    def list_prefix(self, prefix: str):
        paginator = self.s3.get_paginator("list_objects_v2")
    
        files = []
        for page in paginator.paginate(Bucket=self.BUCKET_NAME, Prefix=prefix):
            for obj in page.get("Contents", []):
                files.append(obj["Key"])
    
        return files    
    
    
    def publish_df(
            self,
            dataset: str,
            base_prefix: str,
            chunk_rows: int = 250_000,
            also_write_latest_pointer: bool = True,
            query_path: str | None = None,
            query_params: dict | None = None,
            df: pd.DataFrame | None = None,
            run_id: str | None = None,
            sub_prefix : str| None = None,
            compression: str = "snappy",
            meta: dict | None = None,
        ) -> dict:
        """
        Publish parquet parts either:
          - from an in-memory df (no query)
          - from an EDW query (query_path required)
    
        Backward-compatible artifacts:
          artifacts["parquet_parts"] always exists
    
        Latest pointer:
          {base_prefix}/{dataset}/LATEST.json -> manifest_key
        """
    
        ts = self._utc_ts()
    
        tmp_dir = Path("./tmp")
        tmp_dir.mkdir(parents=True, exist_ok=True)
    
        def upload_df_as_parquet_parts(df_: pd.DataFrame, prefix_: str, part_i_start: int = 0) -> tuple[list[dict], int, int]:
            """
            Uploads ONE df as parquet parts (chunked by chunk_rows).
            Returns: (parts, row_count, next_part_i)
            """
            parts_: list[dict] = []
            row_count_ = len(df_)
            part_i = part_i_start
    
            for start in range(0, len(df_), chunk_rows):
                chunk = df_.iloc[start:start + chunk_rows]
                local_part = tmp_dir / f"part-{part_i:03d}.parquet"
                chunk.to_parquet(local_part, index=False, compression=compression)
    
                key_part = prefix_ + f"part-{part_i:03d}.parquet"
                self.s3.upload_file(str(local_part), self.BUCKET_NAME, key_part)
                h = self._head(key_part)
    
                parts_.append({
                    "key": key_part,
                    "bytes": h.get("ContentLength"),
                    "etag": h.get("ETag"),
                    "rows": len(chunk),
                    "part": part_i,
                })
    
                # delete local file to avoid disk fill
                try:
                    local_part.unlink()
                except Exception:
                    pass
    
                part_i += 1
    
            return parts_, row_count_, part_i
    

        if df is not None:
            rid = run_id or ts.replace(":", "").replace("-", "")
            prefix = f"{base_prefix}/{dataset}/run_id={rid}/"
            if sub_prefix is not None:
                prefix = prefix + sub_prefix + "/"
                
            key_manifest = prefix + "manifest.json"
            parts: list[dict] = []
            row_count = 0
            part_i = 0
    
            if isinstance(df, (list, tuple)):
                for part_df in df:
                    if part_df is None or len(part_df) == 0:
                        continue
                    part_parts, part_rows, part_i = upload_df_as_parquet_parts(part_df, prefix, part_i_start=part_i)
                    parts.extend(part_parts)
                    row_count += part_rows
            else:
                part_parts, part_rows, _next_part_i = upload_df_as_parquet_parts(df, prefix, part_i_start=0)
                parts = part_parts
                row_count = part_rows
    
            artifacts = {"parquet_parts": parts}
    
            manifest = {
                "dataset": dataset,
                "kind": "parquet_dataset",
                "created_at_utc": ts,
                "run_id": rid,
                "s3": {"bucket": self.BUCKET_NAME, "prefix": prefix},
                "row_count": row_count,
                "artifacts": artifacts,
            }
            if meta:
                manifest["meta"] = meta
    
            self._upload_bytes(
                key_manifest,
                json.dumps(manifest, indent=2).encode("utf-8"),
                content_type="application/json",
            )
    
            if also_write_latest_pointer:
                latest_key = f"{base_prefix}/{dataset}/LATEST.json"
                latest = {
                    "dataset": dataset,
                    "manifest_key": key_manifest,
                    "updated_at_utc": ts,
                    "run_id": rid,
                }
                self._upload_bytes(
                    latest_key,
                    json.dumps(latest, indent=2).encode("utf-8"),
                    content_type="application/json",
                )
                manifest["latest_pointer_key"] = latest_key
    
            print(f"Published parquet run under: {prefix}")
            print(f"- parts: {len(parts)}  rows: {row_count}")
            print(f"- manifest: {key_manifest}")
            return manifest
    

        if not query_path:
            raise ValueError("query_path must be provided when df is None")
    
        q_text = Path(query_path).read_text(encoding="utf-8")
        q_hash = self._sha256_text(q_text + json.dumps(query_params or {}, sort_keys=True))
    
        prefix = f"{base_prefix}/{dataset}/query_hash={q_hash}/extracted_at={ts}/"
        key_query = prefix + "query.sql"
        key_params = prefix + "params.json"
        key_manifest = prefix + "manifest.json"
    
        self._upload_bytes(key_query, q_text.encode("utf-8"), content_type="text/plain; charset=utf-8")
        q_head = self._head(key_query)
    
        if query_params:
            self._upload_bytes(
                key_params,
                json.dumps(query_params, indent=2).encode("utf-8"),
                content_type="application/json",
            )
    
        artifacts = {
            "query_sql": {"key": key_query, "bytes": q_head.get("ContentLength"), "etag": q_head.get("ETag")},
        }
        if query_params:
            artifacts["params_json"] = {"key": key_params}
    
        parts: list[dict] = []
        row_count = 0
        mode = "extract_from_edw_to_parquet"
    
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.arraysize = min(chunk_rows, 100_000)
            cur.execute(q_text, query_params or {})
    
            colnames = [d[0] for d in cur.description]
            part_i = 0
    
            while True:
                rows = cur.fetchmany(chunk_rows)
                if not rows:
                    break
    
                df_chunk = pd.DataFrame.from_records(rows, columns=colnames)
                row_count += len(df_chunk)
    
                local_part = tmp_dir / f"part-{part_i:03d}.parquet"
                df_chunk.to_parquet(local_part, index=False, compression=compression)
    
                key_part = prefix + f"part-{part_i:03d}.parquet"
                self.s3.upload_file(str(local_part), self.BUCKET_NAME, key_part)
                h = self._head(key_part)
    
                parts.append({
                    "key": key_part,
                    "bytes": h.get("ContentLength"),
                    "etag": h.get("ETag"),
                    "rows": len(df_chunk),
                    "part": part_i,
                })
    
                try:
                    local_part.unlink()
                except Exception:
                    pass
    
                part_i += 1
    
        finally:
            try:
                conn.close()
            except Exception:
                pass
    
        artifacts["parquet_parts"] = parts
    
        manifest = {
            "dataset": dataset,
            "source": "EDW_PROD",
            "mode": mode,
            "query_file_local": query_path,
            "query_hash": f"sha256:{q_hash}",
            "extracted_at_utc": ts,
            "s3": {"bucket": self.BUCKET_NAME, "prefix": prefix},
            "row_count": row_count,
            "artifacts": artifacts,
        }
        if meta:
            manifest["meta"] = meta
    
        self._upload_bytes(
            key_manifest,
            json.dumps(manifest, indent=2).encode("utf-8"),
            content_type="application/json",
        )
    
        if also_write_latest_pointer:
            latest_key = f"{base_prefix}/{dataset}/LATEST.json"
            latest = {
                "dataset": dataset,
                "manifest_key": key_manifest,
                "updated_at_utc": ts,
                "query_hash": f"sha256:{q_hash}",
            }
            self._upload_bytes(
                latest_key,
                json.dumps(latest, indent=2).encode("utf-8"),
                content_type="application/json",
            )
            manifest["latest_pointer_key"] = latest_key
    
        print(f"Published parquet-only run under: {prefix}")
        print(f"- parts: {len(parts)}  rows: {row_count}")
        print(f"- manifest: {key_manifest}")
    
        return manifest

    def publish_model_results(
        self,
        dataset: str,
        base_prefix: str = "sdp",
        results: dict | None = None,
        chunk_rows: int = 250_000,
        also_write_latest_pointer: bool = True,
        run_id: str | None = None,
        compression: str = "snappy",
        meta: dict | None = None,
    ) -> dict:
        """
        Publish a training results dict to S3.
    
        - DataFrames: stored as parquet datasets using existing publish_df
          (one dataset per DF: f"{dataset}__{name}")
        - Non-DF objects: stored as pickle (or catboost .cbm when available)
    
        Writes:
          {base_prefix}/{dataset}/run_id=.../manifest.json
          {base_prefix}/{dataset}/LATEST.json (optional)
        """
        if results is None:
            results = {}
    
        ts = self._utc_ts()
        rid = run_id or self._safe_ts_for_path(ts)
        run_prefix = f"{base_prefix}/{dataset}/run_id={rid}/"
        key_manifest = run_prefix + "manifest.json"
    
        tmp_dir = Path("./tmp")
        tmp_dir.mkdir(parents=True, exist_ok=True)
    
        artifacts = {
            "dataframes": {},   
            "objects": {},     
        }
    
        for name, obj in results.items():
            if isinstance(obj, pd.DataFrame):
                df_dataset = f"{dataset}"
                df_manifest = self.publish_df(
                    dataset=df_dataset,
                    base_prefix=base_prefix,
                    chunk_rows=chunk_rows,
                    also_write_latest_pointer=False,  
                    df=obj,
                    run_id=rid, 
                    sub_prefix=f"dataframes/{name}",
                    compression=compression,
                    meta={"parent_dataset": dataset, "artifact_name": name},
                )

                artifacts["dataframes"][name] = {
                    "dataset": df_dataset,
                    "manifest_key": f"{base_prefix}/{df_dataset}/run_id={rid}/manifest.json",
                    "s3_prefix": df_manifest["s3"]["prefix"],
                    "row_count": df_manifest.get("row_count"),
                }
    
        for name, obj in results.items():
            if obj is None or isinstance(obj, pd.DataFrame):
                continue

            if hasattr(obj, "save_model"):
                local_path = tmp_dir / f"{name}.cbm"
                try:
                    obj.save_model(str(local_path))
                    key_obj = run_prefix + f"objects/{name}.cbm"
                    self.s3.upload_file(str(local_path), self.BUCKET_NAME, key_obj)
                    h = self._head(key_obj)
                    artifacts["objects"][name] = {
                        "key": key_obj,
                        "bytes": h.get("ContentLength"),
                        "etag": h.get("ETag"),
                        "format": "catboost_cbm",
                    }
                    try:
                        local_path.unlink()
                    except Exception:
                        pass
                    continue
                except Exception:
                    try:
                        local_path.unlink()
                    except Exception:
                        pass
    
            local_path = tmp_dir / f"{name}.pkl"
            with open(local_path, "wb") as f:
                pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
    
            key_obj = run_prefix + f"objects/{name}.pkl"
            self.s3.upload_file(str(local_path), self.BUCKET_NAME, key_obj)
            h = self._head(key_obj)
    
            artifacts["objects"][name] = {
                "key": key_obj,
                "bytes": h.get("ContentLength"),
                "etag": h.get("ETag"),
                "format": "pickle",
            }
    
            try:
                local_path.unlink()
            except Exception:
                pass
    
        manifest = {
            "dataset": dataset,
            "kind": "model_results",
            "created_at_utc": ts,
            "run_id": rid,
            "s3": {"bucket": self.BUCKET_NAME, "prefix": run_prefix},
            "artifacts": artifacts,
        }
        if meta:
            manifest["meta"] = meta
    
        self._upload_bytes(
            key_manifest,
            json.dumps(manifest, indent=2).encode("utf-8"),
            content_type="application/json",
        )
    
        if also_write_latest_pointer:
            latest_key = f"{base_prefix}/{dataset}/LATEST.json"
            latest = {
                "dataset": dataset,
                "manifest_key": key_manifest,
                "updated_at_utc": ts,
                "run_id": rid,
            }
            self._upload_bytes(
                latest_key,
                json.dumps(latest, indent=2).encode("utf-8"),
                content_type="application/json",
            )
            manifest["latest_pointer_key"] = latest_key
    
        print(f"Published model results under: {run_prefix}")
        print(f"- manifest: {key_manifest}")
        return manifest
    
    def get_json(self, key: str) -> dict:
        obj = self.s3.get_object(Bucket=self.BUCKET_NAME, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))

    def get_latest_manifest(self, dataset: str, base_prefix: str = "sdp") -> dict:
        latest_key = f"{base_prefix}/{dataset}/LATEST.json"
        latest = self.get_json(latest_key)
        manifest_key = latest["manifest_key"]
        manifest = self.get_json(manifest_key)

        manifest["_latest_key"] = latest_key
        manifest["_manifest_key"] = manifest_key
        return manifest

    def get_latest_parquet_keys(self, dataset: str, base_prefix: str = "sdp") -> list[str]:
        manifest = self.get_latest_manifest(dataset, base_prefix=base_prefix)
        parts = manifest["artifacts"]["parquet_parts"]
        return [p["key"] for p in parts]


    def read_bytes(self, key: str) -> bytes:
        obj = self.s3.get_object(Bucket=self.BUCKET_NAME, Key=key)
        return obj["Body"].read()

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.read_bytes(key).decode(encoding)

    def read_json(self, key: str) -> dict:
        return json.loads(self.read_text(key))
    
    def load_latest_parquet_to_pandas(self, dataset: str, base_prefix: str = "sdp") -> pd.DataFrame:
        keys = self.get_latest_parquet_keys(dataset, base_prefix=base_prefix)
        dfs = []
        for key in keys:
            b = self.read_bytes(key)
            dfs.append(pd.read_parquet(io.BytesIO(b)))
        return pd.concat(dfs, ignore_index=True)
    
    def load_latest_model(
        self,
        dataset: str = "model_saves",
        base_prefix: str = "sdp/outputs",
        load_dataframes: bool = True,
    ) -> dict:
        """
        Loads latest model bundle under:
          {base_prefix}/{dataset}/LATEST.json -> manifest_key (run_id/manifest.json)
    
        Expected layout (your example):
          .../run_id=.../manifest.json
          .../run_id=.../objects/...
          .../run_id=.../dataframes/{df_name}/manifest.json
          .../run_id=.../dataframes/{df_name}/part-XYZ.parquet
    
        Returns a dict with keys like:
          {"model": ..., "scaler": ..., "X_train": df, "train_data": df, ...}
        """

        latest_key = f"{base_prefix}/{dataset}/LATEST.json"
        latest = self.get_json(latest_key)
        model_manifest_key = latest["manifest_key"]
        model_manifest = self.get_json(model_manifest_key)
    
        run_prefix = model_manifest["s3"]["prefix"]  # should end with /run_id=.../
        out: dict = {}
    
        obj_prefix = run_prefix + "objects/"
        obj_keys = self.list_prefix(obj_prefix)
    
        for key in obj_keys:
            fname = key.split("/")[-1]              
            name, ext = fname.rsplit(".", 1)        
    
            b = self.read_bytes(key)
    
            if ext == "pkl":
                out[name] = pickle.loads(b)
    
            elif ext == "cbm":
                from catboost import CatBoostClassifier
                tmp_dir = Path("./tmp")
                tmp_dir.mkdir(parents=True, exist_ok=True)
                local_path = tmp_dir / fname
                with open(local_path, "wb") as f:
                    f.write(b)
                m = CatBoostClassifier()
                m.load_model(str(local_path))
                out[name] = m
                try:
                    local_path.unlink()
                except Exception:
                    pass
    
            else:
                out[name] = b

        if load_dataframes:
            df_manifest_prefix = run_prefix + "dataframes/"
            df_keys = self.list_prefix(df_manifest_prefix)
    
            df_manifest_keys = [k for k in df_keys if k.endswith("/manifest.json")]
    
            for mkey in df_manifest_keys:
                parts = mkey.split("/dataframes/", 1)[1].split("/", 1)
                df_name = parts[0]
    
                df_manifest = self.get_json(mkey)
                parquet_parts = df_manifest.get("artifacts", {}).get("parquet_parts", [])
                part_keys = [p["key"] for p in parquet_parts]
    
                dfs = []
                for pk in part_keys:
                    b = self.read_bytes(pk)
                    dfs.append(pd.read_parquet(io.BytesIO(b)))
    
                out[df_name] = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    
        out["_latest_key"] = latest_key
        out["_model_manifest_key"] = model_manifest_key
        out["_run_prefix"] = run_prefix
    
        return out
        
    def get_data(self, dataset, query=None, query_params=None, chunk_rows=1_000_000, base_prefix="sdp"):

        if self.APP_ENV in ["DEV", "TEST"]:
            data = self.load_latest_parquet_to_pandas(dataset=dataset, base_prefix=base_prefix)
        else:
            if query is not None:
                # query, ya bir .sql dosya yolu (legacy TRP pattern) ya da
                # ham SQL string olabilir (LLM-driven block pipeline).
                try:
                    is_path = os.path.isfile(query)
                except (OSError, ValueError, TypeError):
                    is_path = False

                if is_path:
                    with open(query, "r") as sql:
                        query_string = sql.read()
                else:
                    query_string = query

                conn = self.get_connection()
                cur = conn.cursor()
                cur.execute(query_string, query_params or {})

                colnames = [d[0] for d in cur.description]
                all_rows = []

                while True:
                    rows = cur.fetchmany(chunk_rows)
                    if not rows:
                        break
                    all_rows.extend(rows)

                data = pd.DataFrame.from_records(all_rows, columns=colnames)
            else:
                raise Exception("Query path is None or wrong!")

        return data
