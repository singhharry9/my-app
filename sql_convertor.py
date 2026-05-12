import streamlit as st
import pandas as pd
import io

# =============================
# 🎨 PAGE CONFIG + THEME
# =============================
st.set_page_config(
    page_title="SQL Converter",
    page_icon="📊",
    layout="wide"
)

# --- Custom Blue Professional Theme ---
st.markdown("""
<style>

/* App background */
.stApp {
    background-color: #0b1220;
    color: #e6f0ff;
}

/* Main title */
h1 {
    color: #4da3ff;
    text-align: center;
    font-weight: 800;
}

/* Sub headers */
h2, h3 {
    color: #7ab8ff;
}

/* File uploader */
[data-testid="stFileUploader"] {
    background-color: #111a2e;
    border: 1px solid #2b4c7e;
    border-radius: 12px;
    padding: 15px;
}

/* Input fields */
input {
    background-color: #111a2e !important;
    color: white !important;
    border-radius: 8px !important;
    border: 1px solid #2b4c7e !important;
}

/* Buttons */
.stDownloadButton button {
    background: linear-gradient(90deg, #1e90ff, #4da3ff);
    color: white;
    border-radius: 10px;
    padding: 8px 16px;
    border: none;
    font-weight: bold;
}

.stDownloadButton button:hover {
    background: linear-gradient(90deg, #4da3ff, #1e90ff);
    transform: scale(1.03);
}

/* Dataframe */
[data-testid="stDataFrame"] {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid #2b4c7e;
}

/* SQL code block */
.stCodeBlock {
    background-color: #0f1a33 !important;
    border-radius: 10px;
    border: 1px solid #2b4c7e;
}

</style>
""", unsafe_allow_html=True)

# =============================
# 🏷️ TITLE
# =============================
st.markdown("""
# 📄 SQL Converter
### 🚀 Fast • Clean • Professional SQL Generator
""", unsafe_allow_html=True)


# =============================
# 🔧 SQL TYPE INFERENCE
# =============================
def infer_sql_type(dtype):
    if pd.api.types.is_integer_dtype(dtype):
        return "INT"
    elif pd.api.types.is_float_dtype(dtype):
        return "FLOAT"
    elif pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"
    elif pd.api.types.is_datetime64_any_dtype(dtype):
        return "DATE"
    else:
        return "TEXT"


# =============================
# 🧠 SQL GENERATOR
# =============================
def dataframe_to_sql(df, table_name):
    sql = f"CREATE TABLE `{table_name}` (\n"

    for col in df.columns:
        col_type = infer_sql_type(df[col].dtype)
        sql += f"  `{col}` {col_type},\n"

    sql = sql.rstrip(",\n") + "\n);\n\n"

    for _, row in df.iterrows():
        values = []
        for val in row:
            if pd.isna(val):
                values.append("NULL")
            elif isinstance(val, pd.Timestamp):
                values.append(f"'{val.strftime('%Y-%m-%d')}'")
            elif isinstance(val, str):
                values.append("'" + val.replace("'", "''") + "'")
            else:
                values.append(str(val))

        sql += f"INSERT INTO `{table_name}` VALUES ({', '.join(values)});\n"

    return sql


# =============================
# 📤 FILE UPLOAD
# =============================
uploaded_file = st.file_uploader(
    "📂 Upload CSV, Excel, or ODS file",
    type=["csv", "xlsx", "ods"]
)

table_name = st.text_input("🗄️ Enter SQL Table Name", value="my_table")


# =============================
# 🚀 MAIN LOGIC
# =============================
if uploaded_file and table_name:

    try:
        # --- Load Data ---
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file, parse_dates=True)
        elif uploaded_file.name.endswith(".xlsx"):
            df = pd.read_excel(uploaded_file, engine="openpyxl", parse_dates=True)
        elif uploaded_file.name.endswith(".ods"):
            df = pd.read_excel(uploaded_file, engine="odf", parse_dates=True)
        else:
            st.error("❌ Unsupported file format")
            st.stop()

        # =============================
        # 📋 PREVIEW
        # =============================
        st.subheader("📋 Data Preview")
        st.dataframe(df.head(), use_container_width=True)

        # =============================
        # 🧾 SQL GENERATION
        # =============================
        sql_code = dataframe_to_sql(df, table_name)

        st.subheader("📝 Generated SQL")
        st.code(sql_code[:1500] + ("..." if len(sql_code) > 1500 else ""), language="sql")

        # =============================
        # 📥 DOWNLOAD SQL
        # =============================
        sql_bytes = io.BytesIO(sql_code.encode("utf-8"))

        st.download_button(
            label="📥 Download SQL File",
            data=sql_bytes,
            file_name=f"{table_name}.sql",
            mime="text/sql"
        )

        # =============================
        # 📥 DOWNLOAD CSV
        # =============================
        csv_bytes = io.BytesIO()
        df.to_csv(csv_bytes, index=False)
        csv_bytes.seek(0)

        st.download_button(
            label="📥 Download CSV File",
            data=csv_bytes,
            file_name=f"{table_name}.csv",
            mime="text/csv"
        )

    except Exception as e:
        st.error(f"❌ Error: {e}")
