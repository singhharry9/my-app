import streamlit as st
import json
import pandas as pd
import re
from io import StringIO

st.set_page_config(
    page_title="JSON to SQL / CSV Converter",
    page_icon="🧩",
    layout="wide"
)

st.markdown("""
<style>
    .main {
        background-color: #f7f9fc;
    }

    .title-box {
        background: linear-gradient(135deg, #1e3a8a, #2563eb);
        padding: 35px;
        border-radius: 18px;
        color: white;
        margin-bottom: 25px;
        box-shadow: 0 8px 25px rgba(0,0,0,0.15);
    }

    .title-box h1 {
        margin: 0;
        font-size: 38px;
        font-weight: 800;
    }

    .title-box p {
        margin-top: 10px;
        font-size: 16px;
        opacity: 0.95;
    }

    .card {
        background-color: white;
        padding: 25px;
        border-radius: 16px;
        box-shadow: 0 4px 18px rgba(0,0,0,0.08);
        margin-bottom: 20px;
    }

    .metric-card {
        background: white;
        padding: 18px;
        border-radius: 14px;
        text-align: center;
        box-shadow: 0 4px 14px rgba(0,0,0,0.07);
    }

    .metric-card h3 {
        margin: 0;
        color: #1e3a8a;
        font-size: 26px;
    }

    .metric-card p {
        margin: 4px 0 0 0;
        color: #64748b;
        font-size: 14px;
    }

    div.stButton > button {
        background: linear-gradient(135deg, #2563eb, #1d4ed8);
        color: white;
        border-radius: 10px;
        padding: 12px 22px;
        font-weight: 700;
        border: none;
        width: 100%;
    }

    div.stButton > button:hover {
        background: linear-gradient(135deg, #1d4ed8, #1e40af);
        color: white;
    }

    div.stDownloadButton > button {
        background: linear-gradient(135deg, #16a34a, #15803d);
        color: white;
        border-radius: 10px;
        padding: 12px 22px;
        font-weight: 700;
        border: none;
        width: 100%;
    }

    textarea {
        font-family: Consolas, monospace !important;
    }
</style>
""", unsafe_allow_html=True)


def clean_identifier(name):
    name = name.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        return None
    return name


def sql_escape(value):
    if pd.isna(value):
        return "NULL"

    if isinstance(value, (int, float)):
        return str(value)

    value = str(value).replace("'", "''")
    return f"'{value}'"


def generate_sql(df, table_name):
    columns = [f"`{col}`" for col in df.columns]
    sql_queries = []

    for _, row in df.iterrows():
        values = [sql_escape(value) for value in row]
        query = (
            f"INSERT INTO `{table_name}` "
            f"({', '.join(columns)}) "
            f"VALUES ({', '.join(values)});"
        )
        sql_queries.append(query)

    return "\n".join(sql_queries)


st.markdown("""
<div class="title-box">
    <h1>JSON to SQL / CSV Converter</h1>
    <p>Upload a JSON file, preview your data, generate SQL insert statements, and export clean CSV files.</p>
</div>
""", unsafe_allow_html=True)

left, right = st.columns([1, 2])

with left:
    st.markdown('<div class="card">', unsafe_allow_html=True)

    st.subheader("Upload & Configuration")

    uploaded_file = st.file_uploader(
        "Upload JSON File",
        type=["json"],
        help="Upload a valid JSON file. Both object and list formats are supported."
    )

    table_name_input = st.text_input(
        "Table Name",
        placeholder="example: customers"
    )

    st.markdown("</div>", unsafe_allow_html=True)

with right:
    if uploaded_file:
        try:
            data = json.load(uploaded_file)

            if not isinstance(data, list):
                data = [data]

            df = pd.json_normalize(data)

            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown(
                    f'<div class="metric-card"><h3>{len(df)}</h3><p>Total Rows</p></div>',
                    unsafe_allow_html=True
                )

            with col2:
                st.markdown(
                    f'<div class="metric-card"><h3>{len(df.columns)}</h3><p>Total Columns</p></div>',
                    unsafe_allow_html=True
                )

            with col3:
                st.markdown(
                    f'<div class="metric-card"><h3>{round(uploaded_file.size / 1024, 2)}</h3><p>File Size KB</p></div>',
                    unsafe_allow_html=True
                )

            st.markdown("### Data Preview")
            st.dataframe(df, use_container_width=True, height=350)

            csv_data = df.to_csv(index=False).encode("utf-8")

            btn_col1, btn_col2 = st.columns(2)

            with btn_col1:
                st.download_button(
                    label="Download CSV",
                    data=csv_data,
                    file_name="converted_output.csv",
                    mime="text/csv"
                )

            with btn_col2:
                convert_clicked = st.button("Generate SQL")

            if convert_clicked:
                table_name = clean_identifier(table_name_input)

                if not table_name:
                    st.error("Please enter a valid table name. Use only letters, numbers, and underscores. It must not start with a number.")
                elif df.empty:
                    st.warning("The uploaded JSON file does not contain any records.")
                else:
                    sql_output = generate_sql(df, table_name)

                    st.markdown("### Generated SQL")
                    st.text_area(
                        "SQL Output",
                        sql_output,
                        height=350
                    )

                    st.download_button(
                        label="Download SQL File",
                        data=sql_output.encode("utf-8"),
                        file_name=f"{table_name}_insert_queries.sql",
                        mime="application/sql"
                    )

        except json.JSONDecodeError:
            st.error("Invalid JSON file. Please upload a properly formatted JSON file.")

        except Exception as e:
            st.error(f"Something went wrong: {e}")

    else:
        st.markdown("""
        <div class="card">
            <h3>How it works</h3>
            <p>1. Upload your JSON file.</p>
            <p>2. Enter the target SQL table name.</p>
            <p>3. Preview the converted table.</p>
            <p>4. Download CSV or generate SQL INSERT queries.</p>
        </div>
        """, unsafe_allow_html=True)
