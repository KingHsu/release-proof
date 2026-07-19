from __future__ import annotations

import json
import os
from urllib import error, request

import streamlit as st

st.set_page_config(page_title="ReleaseProof", page_icon="🧾", layout="wide")
st.title("发布验收助手")
st.caption("把验收条件与代码、测试、CI 证据对应起来；最终发布决定始终由人完成。")

api_url = st.sidebar.text_input(
    "API 地址", os.getenv("RELEASE_PROOF_API_URL", "http://127.0.0.1:8002")
).rstrip("/")


def api_call(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        api_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API {exc.code}: {detail[:1000]}") from exc


create_tab, report_tab, eval_tab = st.tabs(["创建分析", "查看报告", "离线评测"])
with create_tab:
    repository = st.text_input("本地 Git 仓库", value="")
    col1, col2 = st.columns(2)
    base_ref = col1.text_input("Base ref", "HEAD~1")
    head_ref = col2.text_input("Head ref", "HEAD")
    requirement = st.text_area(
        "验收条件（Markdown 清单）",
        value="- API 返回健康状态\n- 必须有对应自动化测试",
        height=150,
    )
    report_paths = st.text_area("JUnit/Coverage 报告路径（每行一个，可暂空）", height=80)
    continue_incomplete = st.checkbox("没有测试报告时仍生成不完整报告", value=False)
    if st.button("开始只读分析", type="primary"):
        payload = {
            "repository_path": repository,
            "base_ref": base_ref,
            "head_ref": head_ref,
            "requirement_source": {"kind": "inline", "content": requirement},
            "report_paths": [line.strip() for line in report_paths.splitlines() if line.strip()],
            "mode": "auto",
            "continue_without_reports": continue_incomplete,
        }
        try:
            result = api_call("POST", "/api/v1/analyses", payload)
            st.session_state["last_run"] = result
            st.success(f"运行状态：{result['status']} · {result['run_id']}")
            st.json(result)
        except Exception as exc:
            st.error(str(exc))

with report_tab:
    default_id = st.session_state.get("last_run", {}).get("run_id", "")
    run_id = st.text_input("Run ID", value=default_id)
    if st.button("加载") and run_id:
        try:
            result = api_call("GET", f"/api/v1/analyses/{run_id}")
            report = result.get("report")
            if report:
                st.metric("建议", report["recommendation"])
                st.dataframe(report["acceptance_matrix"], use_container_width=True)
                with st.expander("风险、证据和限制", expanded=True):
                    st.json(report)
            elif result.get("interrupt"):
                st.warning("分析正在等待具体材料。")
                st.json(result["interrupt"])
            else:
                st.json(result)
        except Exception as exc:
            st.error(str(exc))

with eval_tab:
    st.write("使用仓库内固定 fixtures，对比文本直答、单流程和条件式多 Agent 路由。")
    if st.button("运行离线评测"):
        try:
            result = api_call("POST", "/api/v1/evaluations", {})
            st.dataframe(result["metrics"], use_container_width=True)
            st.json(result)
        except Exception as exc:
            st.error(str(exc))
