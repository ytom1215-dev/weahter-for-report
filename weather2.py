import pandas as pd
import streamlit as st
import plotly.graph_objects as go
import io
import re

st.set_page_config(page_title="統合型農業気象分析ツール", layout="wide")
st.title("🚜 統合型農業気象分析ツール (アメダス対応版)")
st.markdown("気象庁からダウンロードした「日別値・平年値入りデータ」を読み込んで分析します。")


# --- 内部関数：農業カレンダー付与 ---
def add_agri_labels(df):
    df = df.copy()
    df['year'] = df['date'].dt.year
    df['month'] = df['date'].dt.month
    day = df['date'].dt.day
    df['jun'] = day.apply(lambda x: 1 if x <= 10 else (2 if x <= 20 else 3))
    df['hanjun'] = day.apply(lambda x: min((x - 1) // 5 + 1, 6))
    df['m_jun'] = df['month'].astype(str).str.zfill(2) + "-" + df['jun'].astype(str)
    df['m_hanjun'] = df['month'].astype(str).str.zfill(2) + "-" + df['hanjun'].astype(str)
    return df


# ============================================================
# 気象庁データを自動解析するパーサー (CSV / タブ区切り 両対応)
# ============================================================
def parse_jma_csv(decoded_text):
    delimiter = '\t' if '\t' in decoded_text else ','

    all_lines = [l for l in decoded_text.splitlines() if l.strip()]

    data_start = None
    for i, line in enumerate(all_lines):
        first = line.split(delimiter)[0].strip().strip('"')
        if not first:
            continue
        if re.match(r'^\d{4}[/-]\d{1,2}[/-]\d{1,2}$', first):
            try:
                if pd.notna(pd.to_datetime(first)):
                    data_start = i
                    break
            except Exception:
                pass

    if data_start is None:
        raise ValueError(
            "日付列を含むデータ行が見つかりません。\n"
            "気象庁からダウンロードしたデータをそのままの形式で入力してください。"
        )

    header_lines = all_lines[max(0, data_start - 4): data_start]
    header_grid = []
    for line in header_lines:
        row = [c.strip().strip('"') for c in line.split(delimiter)]
        header_grid.append(row)

    data_text = '\n'.join(all_lines[data_start:])
    df_raw = pd.read_csv(io.StringIO(data_text), header=None, sep=delimiter)
    n_cols = df_raw.shape[1]

    def ffill_row(row, length):
        result = [''] * length
        last_val = ''
        for i in range(length):
            val = row[i] if i < len(row) else ''
            if val:
                last_val = val
            result[i] = last_val
        return result

    ffilled_grid = []
    for ri, row in enumerate(header_grid):
        if ri == 0:
            ffilled_grid.append(ffill_row(row, n_cols))
        else:
            ffilled_grid.append(row + [''] * (n_cols - len(row)))

    col_labels = []
    for ci in range(n_cols):
        parts = []
        for row in ffilled_grid:
            val = row[ci] if ci < len(row) else ''
            if val and val not in parts:
                parts.append(val)
        col_labels.append('|'.join(parts))

    QUALITY_KEYS = ['品質', '均質']

    def find_col(must, exclude=None):
        if exclude is None:
            exclude = []
        exclude = exclude + QUALITY_KEYS
        for i, label in enumerate(col_labels):
            if all(k in label for k in must) and not any(k in label for k in exclude):
                return i
        return None

    col_map = {
        'temp_mean':        find_col(['気温', '平均'], ['最高', '最低', '平年']),
        'temp_mean_normal': find_col(['気温', '平均', '平年'], ['最高', '最低']),
        'precip':           find_col(['降水量'], ['平年']),
        'precip_normal':    find_col(['降水量', '平年']),
        'temp_max':         find_col(['最高気温'], ['平年']),
        'temp_max_normal':  find_col(['最高気温', '平年']),
        'temp_min':         find_col(['最低気温'], ['平年']),
        'temp_min_normal':  find_col(['最低気温', '平年']),
        'sun_hours':        find_col(['日照時間'], ['平年']),
        'sun_hours_normal': find_col(['日照時間', '平年']),
    }

    valid = {k: v for k, v in col_map.items() if v is not None}
    df_clean = df_raw.iloc[:, [0] + list(valid.values())].copy()
    df_clean.columns = ['date'] + list(valid.keys())

    df_clean['date'] = pd.to_datetime(df_clean['date'], errors='coerce')
    df_clean = df_clean.dropna(subset=['date']).reset_index(drop=True)

    for col in df_clean.columns:
        if col != 'date':
            df_clean[col] = pd.to_numeric(df_clean[col], errors='coerce')

    for col in col_map:
        if col not in df_clean.columns:
            df_clean[col] = float('nan')

    return df_clean, col_labels, col_map


# --- サイドバー：データ入力 ---
st.sidebar.header("📂 1. データ入力")
input_method = st.sidebar.radio("入力方法を選択", ["CSVファイルアップロード", "テキストコピペ (Excel等から)"])

decoded_text = None

if input_method == "CSVファイルアップロード":
    uploaded_file = st.sidebar.file_uploader("気象庁のCSVファイルを選択", type="csv")
    if uploaded_file is not None:
        raw_bytes = uploaded_file.getvalue()
        try:
            decoded_text = raw_bytes.decode('utf-8')
        except UnicodeDecodeError:
            decoded_text = raw_bytes.decode('cp932')
else:
    st.sidebar.markdown("気象庁のデータをExcel等で開き、**見出し行も含めて全選択してコピー**し、下に貼り付けてください。")
    pasted_text = st.sidebar.text_area("ここにデータを貼り付け", height=150)
    if pasted_text.strip():
        decoded_text = pasted_text


# --- メイン処理 ---
if decoded_text is not None:
    try:
        df_clean, col_labels, col_map = parse_jma_csv(decoded_text)

        with st.expander("🔍 列検出ログ（変換がおかしい場合に確認）", expanded=False):
            st.write("**検出された列マッピング:**")
            debug_rows = []
            for k, v in col_map.items():
                label = col_labels[v] if v is not None else "（未検出）"
                debug_rows.append({"変数名": k, "列番号": v, "ヘッダー": label})
            st.dataframe(pd.DataFrame(debug_rows))
            st.write(f"**データ件数:** {len(df_clean)} 行")
            if not df_clean.empty:
                st.write(f"**期間:** {df_clean['date'].min().date()} 〜 {df_clean['date'].max().date()}")

        df = add_agri_labels(df_clean)

        # --- サイドバー：分析設定 ---
        st.sidebar.header("📊 2. 分析設定")
        target_dict = {
            "平均気温": {"actual": "temp_mean", "normal": "temp_mean_normal", "agg": "mean"},
            "最高気温": {"actual": "temp_max",  "normal": "temp_max_normal",  "agg": "mean"},
            "最低気温": {"actual": "temp_min",  "normal": "temp_min_normal",  "agg": "mean"},
            "降水量":   {"actual": "precip",    "normal": "precip_normal",    "agg": "sum"},
            "日照時間": {"actual": "sun_hours", "normal": "sun_hours_normal", "agg": "sum"},
        }

        available_targets = {
            k: v for k, v in target_dict.items()
            if not df[v["actual"]].isna().all()
        }
        if not available_targets:
            st.error("変換できた数値データがありません。データの中に正しく数値が含まれているか確認してください。")
            st.stop()

        target_label = st.sidebar.selectbox("表示指標", list(available_targets.keys()))
        agg_unit = st.sidebar.radio("集計単位", ["日次", "半旬別", "旬別", "月別"])

        col_act  = available_targets[target_label]["actual"]
        col_norm = available_targets[target_label]["normal"]
        agg_func = available_targets[target_label]["agg"]

        # --- 集計処理 ---
        if agg_unit == "日次":
            df_agg = df.copy()
            df_agg['display_name'] = df_agg['date'].dt.strftime('%Y/%m/%d')
        else:
            if agg_unit == "半旬別":
                group_cols = ['year', 'month', 'm_hanjun']
                group_key  = 'm_hanjun'
            elif agg_unit == "旬別":
                group_cols = ['year', 'month', 'm_jun']
                group_key  = 'm_jun'
            else:
                group_cols = ['year', 'month']
                group_key  = 'month'

            df_agg = df.groupby(group_cols)[[col_act, col_norm]].agg(agg_func).reset_index()

            JUN_NAME = {1: "上", 2: "中", 3: "下"}

            def make_label(row):
                y, m = int(row['year']), int(row['month'])
                if agg_unit == "旬別":
                    j = JUN_NAME[int(row[group_key].split("-")[1])]
                    return f"{y}年{m}月 {j}旬"
                elif agg_unit == "半旬別":
                    h = row[group_key].split("-")[1]
                    return f"{y}年{m}月 第{h}半旬"
                return f"{y}年{m}月"

            df_agg['display_name'] = df_agg.apply(make_label, axis=1)

        # --- 年ラベル生成 ---
        years = df['year'].unique()
        if len(years) == 1:
            year_label = f"{years[0]}年値"
        else:
            year_label = f"{years.min()}〜{years.max()}年値"

        # --- グラフ描画 ---
        st.subheader(f"📈 {target_label}の{agg_unit}比較結果")
        fig = go.Figure()

        is_temp = target_label in ["平均気温", "最高気温", "最低気温"]

        if is_temp:
            # 平年値：グレー点線
            fig.add_trace(go.Scatter(
                x=df_agg['display_name'], y=df_agg[col_norm],
                name="平年値",
                mode='lines',
                line=dict(color='gray', dash='dot', width=2),
            ))
            # 実績値：実線＋マーカー
            fig.add_trace(go.Scatter(
                x=df_agg['display_name'], y=df_agg[col_act],
                name=year_label,
                mode='lines+markers',
                line=dict(color='#deff9a', width=2),
                marker=dict(size=5),
            ))
        else:
            # 降水量・日照時間：棒グラフ
            fig.add_trace(go.Bar(
                x=df_agg['display_name'], y=df_agg[col_norm],
                name="平年値", marker_color='gray', opacity=0.5
            ))
            fig.add_trace(go.Bar(
                x=df_agg['display_name'], y=df_agg[col_act],
                name=year_label, marker_color='#deff9a'
            ))

        UNIT_MAP = {
            "平均気温": "℃",
            "最高気温": "℃",
            "最低気温": "℃",
            "降水量":   "mm",
            "日照時間": "時間",
        }
        yaxis_title = f"{target_label}（{UNIT_MAP[target_label]}）"

        fig.update_layout(
            template="plotly_dark",
            hovermode="x unified",
            barmode='group',
            yaxis_title=yaxis_title,
        )
        st.plotly_chart(fig, use_container_width=True)

        # --- 成績書用コメント ---
        st.subheader("📝 成績書用コメント（自動生成）")

        total_act  = df_agg[col_act].sum()  if agg_func == 'sum' else df_agg[col_act].mean()
        total_norm = df_agg[col_norm].sum() if agg_func == 'sum' else df_agg[col_norm].mean()

        if target_label in ["平均気温", "最高気温", "最低気温"]:
            diff_t = total_act - total_norm
            if diff_t >= 1.0:
                trend_t = "かなり高めに推移した"
            elif diff_t >= 0.5:
                trend_t = "高めに推移した"
            elif diff_t <= -1.0:
                trend_t = "かなり低めに推移した"
            elif diff_t <= -0.5:
                trend_t = "低めに推移した"
            else:
                trend_t = "平年並みに推移した"
            summary = (
                f"栽培期間全体の{target_label}は{total_act:.1f}℃"
                f"（平年差{diff_t:+.1f}℃）であり、全体として{trend_t}。"
            )
        else:
            u = "mm" if target_label == "降水量" else "時間"
            r = (total_act / total_norm * 100) if total_norm > 0 else 0
            if r >= 120:
                trend_t = "かなり多く推移した"
            elif r >= 110:
                trend_t = "多く推移した"
            elif r <= 80:
                trend_t = "かなり少なく推移した"
            elif r <= 90:
                trend_t = "少なく推移した"
            else:
                trend_t = "平年並みに推移した"
            summary = (
                f"栽培期間全体の{target_label}は{total_act:.1f}{u}"
                f"（平年比{r:.0f}%）であり、全体として{trend_t}。"
            )

        df_text = (
            df.groupby(['year', 'month'])[[col_act, col_norm]]
            .agg(agg_func)
            .reset_index()
            .sort_values(['year', 'month'])
        )

        trends = []
        for _, row in df_text.iterrows():
            m_s = f"{int(row['month'])}月"
            if target_label in ["平均気温", "最高気温", "最低気温"]:
                d = row[col_act] - row[col_norm]
                if d >= 1.0:
                    s = "高め"
                elif d >= 0.5:
                    s = "高め"
                elif d <= -1.0:
                    s = "低め"
                elif d <= -0.5:
                    s = "低め"
                else:
                    s = "平年並み"
                trends.append({'month': m_s, 'status': s, 'detail': d})
            else:
                r_m = (row[col_act] / row[col_norm] * 100) if row[col_norm] > 0 else 0
                if r_m >= 110:
                    s = "多め"
                elif r_m <= 90:
                    s = "少なめ"
                else:
                    s = "平年並み"
                trends.append({'month': m_s, 'status': s, 'detail': r_m})

        def build_phrase(label, start, end, status, details, is_last=False):
            period = f"{start}から{end}" if start != end else start
            if status == "平年並み":
                base = f"{period}は平年並み"
            elif label in ["平均気温", "最高気温", "最低気温"]:
                min_d = min(abs(d) for d in details)
                max_d = max(abs(d) for d in details)
                v = (f"{min_d:.1f}℃程度" if f"{min_d:.1f}" == f"{max_d:.1f}"
                     else f"{min_d:.1f}〜{max_d:.1f}℃程度")
                direction = "高く" if status == "高め" else "低く"
                base = f"{period}は平年より{v}{direction}"
            else:
                min_r = min(details)
                max_r = max(details)
                v = (f"平年比{min_r:.0f}%程度" if f"{min_r:.0f}" == f"{max_r:.0f}"
                     else f"平年比{min_r:.0f}〜{max_r:.0f}%程度")
                direction = "多く" if status == "多め" else "少なく"
                base = f"{period}は{v}と{direction}"

            if is_last:
                return base + "推移した"
            else:
                return base + ("で" if status == "平年並み" else "推移し")

        grouped = []
        if trends:
            curr_s  = trends[0]['status']
            start_m = trends[0]['month']
            end_m   = trends[0]['month']
            dets    = [trends[0]['detail']]

            for t in trends[1:]:
                if t['status'] == curr_s:
                    end_m = t['month']
                    dets.append(t['detail'])
                else:
                    grouped.append(build_phrase(target_label, start_m, end_m, curr_s, dets))
                    curr_s, start_m, end_m = t['status'], t['month'], t['month']
                    dets = [t['detail']]

            grouped.append(build_phrase(target_label, start_m, end_m, curr_s, dets, is_last=True))

        report = f"{summary}\n詳細を見ると、{'、'.join(grouped)}。"
        st.code(report, language="text")
        st.caption("※右上のアイコンでコピー可能")

    except Exception as e:
        st.error(f"エラーが発生しました: {e}")
        st.info("デバッグ情報を表示するには、エラー詳細を展開してください。")
        with st.expander("エラー詳細"):
            import traceback
            st.code(traceback.format_exc())
else:
    if input_method == "CSVファイルアップロード":
        st.info("左のサイドバーから気象庁のCSVをアップロードしてください。")
    else:
        st.info("左のサイドバーのテキストエリアにデータを貼り付けてください。")