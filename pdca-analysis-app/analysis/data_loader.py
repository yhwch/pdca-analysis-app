"""
File loading utilities for the PDCA application.

Production data arrives from the injection-moulding line as Excel exports
(.xlsx / .xlsm) or CSV files. This module abstracts the format differences so
the rest of the application works with a plain pandas DataFrame regardless of
the source format.

Multi-sheet Excel files require the user to select a sheet before the data can
be loaded (see get_excel_sheet_names). Single-sheet files and CSVs are loaded
directly.
"""

import pandas as pd


def get_excel_sheet_names(uploaded_file):
    """
    Return all sheet names from an Excel workbook.

    Called before load_data when the uploaded file is an Excel format, so the
    UI can present a sheet selector if more than one sheet exists.

    Args:
        uploaded_file: File-like object (BytesIO or Django InMemoryUploadedFile).

    Returns:
        List of sheet name strings in workbook order.
    """
    excel_file = pd.ExcelFile(uploaded_file)
    return excel_file.sheet_names


def load_data(uploaded_file, sheet_name=None, file_name=None):
    """
    Load a production data file into a pandas DataFrame.

    Supports CSV, XLSX, and XLSM formats. The file_name parameter is used to
    determine the format when the file object does not carry a .name attribute
    (e.g. when reading from a BytesIO buffer reconstructed from the session).

    Args:
        uploaded_file: File-like object containing the raw data.
        sheet_name: Sheet to load for Excel files. If None, the default (first)
            sheet is loaded.
        file_name: Optional filename string for format detection. Takes priority
            over uploaded_file.name when provided.

    Returns:
        pandas DataFrame with raw column names and data types from the file.

    Raises:
        ValueError: If the file extension is not .csv, .xlsx, or .xlsm.
    """
    if file_name is None:
        file_name = getattr(uploaded_file, 'name', '').lower()
    else:
        file_name = file_name.lower()

    if file_name.endswith('.csv'):
        return pd.read_csv(uploaded_file)

    if file_name.endswith('.xlsx') or file_name.endswith('.xlsm'):
        return pd.read_excel(uploaded_file, sheet_name=sheet_name)

    raise ValueError(
        'Unsupported file type. Please upload a CSV, XLSX, or XLSM file.'
    )
