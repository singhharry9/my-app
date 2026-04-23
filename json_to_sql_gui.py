import streamlit as st
import json
import pandas as pd

st.title("JSON to SQL / CSV Converter")

# Upload file
uploaded_file = st.file_uploader("Upload JSON file", type=["json"])

table_name = st.text_input("Enter Table Name")

if uploaded_file:
    data = json.load(uploaded_file)

    if not isinstance(data, list):
        data = [data]

    df = pd.DataFrame(data)

    st.write("Preview Data:")
    st.dataframe(df)

    # SQL Generator
    if st.button("Convert to SQL"):
        sql_queries = []
        cols = df.columns

        for _, row in df.iterrows():
            values = ", ".join([f"'{str(v)}'" for v in row])
            query = f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES ({values});"
            sql_queries.append(query)

        st.text_area("Generated SQL", "\n".join(sql_queries), height=300)

    # CSV Download
    csv = df.to_csv(index=False).encode('utf-8')
    st.download_button("Download CSV", csv, "output.csv", "text/csv")
