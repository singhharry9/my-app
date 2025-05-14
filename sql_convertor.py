import streamlit as st
import pandas as pd
import io

# --- SQL Type Inference ---
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

# --- Generate SQL ---
def dataframe_to_sql(df, table_name):
    sql = f"CREATE TABLE {table_name} (\n"
    for col in df.columns:
        col_type = infer_sql_type(df[col].dtype)
        sql += f"  {col} {col_type},\n"
    sql = sql.rstrip(",\n") + "\n);\n\n"

    for _, row in df.iterrows():
        values = []
        for val in row:
            if pd.isna(val):
                values.append("NULL")
            elif isinstance(val, str):
                values.append("'" + val.replace("'", "''") + "'")
            else:
                values.append(str(val))
        sql += f"INSERT INTO {table_name} VALUES ({', '.join(values)});\n"
    return sql

# --- Streamlit UI ---
st.title(" My Converter")

uploaded_file = st.file_uploader("Upload a CSV, Excel, or ODS file", type=["csv", "xlsx", "ods"])
table_name = st.text_input("Enter SQL Table Name", value="my_table")

if uploaded_file and table_name:
    try:
        # Read the uploaded file
        if uploaded_file.name.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        elif uploaded_file.name.endswith(".xlsx"):
            df = pd.read_excel(uploaded_file, engine='openpyxl')
        elif uploaded_file.name.endswith(".ods"):
            df = pd.read_excel(uploaded_file, engine='odf')
        else:
            st.error("Unsupported file format")

        st.subheader("📋 Preview Data")
        st.dataframe(df.head())

        # Generate SQL
        sql_code = dataframe_to_sql(df, table_name)

        st.subheader("📝 SQL Output")
        st.code(sql_code[:1000] + ("..." if len(sql_code) > 1000 else ""), language="sql")

        # Download Button
        sql_bytes = io.BytesIO(sql_code.encode("utf-8"))
        st.download_button(
            label="📥 Download SQL File",
            data=sql_bytes,
            file_name=f"{table_name}.sql",
            mime="text/sql"
        )

    # --- Download CSV ---
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
