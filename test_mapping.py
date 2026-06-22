import os
import unittest
import base64
import json

# Set SQLite database URL for tests to avoid PostgreSQL requirement
os.environ["DATABASE_URL"] = "sqlite:///test_nykaa.db"

import database
from database import init_db, SessionLocal, DbFile, CategoryConfig, SizeMapping, ProcessingJob
import mapping_engine
from mapping_engine import learn_from_historical_excel, generate_nykaa_template, EngineLogger

class TestNykaaMapping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Initialize SQLite database
        init_db()
        
    def setUp(self):
        self.db = SessionLocal()
        # Clean up database
        self.db.query(DbFile).delete()
        self.db.query(CategoryConfig).delete()
        self.db.query(SizeMapping).delete()
        self.db.query(ProcessingJob).delete()
        self.db.commit()

    def tearDown(self):
        self.db.close()

    @classmethod
    def tearDownClass(cls):
        # Clean up SQLite file
        if os.path.exists("test_nykaa.db"):
            try:
                os.remove("test_nykaa.db")
            except:
                pass

    def test_01_learn_from_historical(self):
        logger = EngineLogger()
        
        # We use tshirts_generated.xlsm as historical sheet
        historical_path = "Raw Files/tshirts_generated.xlsm"
        self.assertTrue(os.path.exists(historical_path), f"File {historical_path} not found.")
        
        with open(historical_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("utf-8")
            
        # Run learning engine
        learn_from_historical_excel(self.db, content_b64, "tshirts_generated.xlsm", "Tshirts", logger)
        
        # Verify category configuration is saved
        config = self.db.query(CategoryConfig).filter(CategoryConfig.category_name == "Tshirts").first()
        self.assertIsNotNone(config)
        self.assertIn("Brand Name", config.hardcoded_values)
        self.assertEqual(config.hardcoded_values["Brand Name"], "Purple United Kids")
        self.assertEqual(config.hardcoded_values["Manufacturer Name"], "Purple United Sales Ltd.")
        self.assertEqual(config.hardcoded_values["Country of Origin"], "India")
        
        # Verify size charts were extracted
        sizes = self.db.query(SizeMapping).filter(SizeMapping.category_name == "Tshirts").all()
        self.assertGreater(len(sizes), 0)
        
        # Check specific size measurements (e.g. 3-4Y should have Chest = 36)
        size_3_4 = self.db.query(SizeMapping).filter(
            SizeMapping.category_name == "Tshirts",
            SizeMapping.brand_size == "3-4Y"
        ).first()
        self.assertIsNotNone(size_3_4)
        self.assertEqual(int(size_3_4.measurements.get("Chest for Garment (Inches)")), 36)
        self.assertEqual(int(size_3_4.measurements.get("Waist for Garment (Inches)")), 27)

    def test_02_generate_template(self):
        logger = EngineLogger()
        
        # Prepare Active Item Directory and Content Sheet
        dir_path = "Raw Files/ITEM DIRECTORY Main.xlsx"
        content_path = "Raw Files/Content Sheet.xlsx"
        template_path = "Raw Files/tshirts_generated.xlsm" # acts as category template base
        
        self.assertTrue(os.path.exists(dir_path))
        self.assertTrue(os.path.exists(content_path))
        self.assertTrue(os.path.exists(template_path))
        
        # 1. Upload & Activate files
        with open(dir_path, "rb") as f:
            dir_b64 = base64.b64encode(f.read()).decode("utf-8")
        dir_file = DbFile(file_type="item_directory", filename="item_dir.xlsx", content_b64=dir_b64, is_active=True)
        self.db.add(dir_file)
        
        with open(content_path, "rb") as f:
            content_b64 = base64.b64encode(f.read()).decode("utf-8")
        content_file = DbFile(file_type="content_sheet", filename="content_sheet.xlsx", content_b64=content_b64, is_active=True)
        self.db.add(content_file)
        
        with open(template_path, "rb") as f:
            temp_b64 = base64.b64encode(f.read()).decode("utf-8")
        temp_file = DbFile(file_type="category_template", filename="tshirt_template.xlsm", content_b64=temp_b64, is_active=False)
        self.db.add(temp_file)
        self.db.commit()
        
        # 2. Setup Category Config
        config = CategoryConfig(
            category_name="Tshirts",
            template_file_id=temp_file.id,
            hardcoded_values={
                "Brand Name": "Purple United Kids",
                "Manufacturer Name": "Purple United Sales Ltd.",
                "Manufacturer Address": "Kh. No. 55/14 And 55/15, Mundka, Delhi 110041",
                "Multipack Set": "Single",
                "Ships In Days": 1,
                "Net Qty": "1N",
                "Material": "Cotton"
            },
            column_mappings={
                "Vendor SKU Code": "ITEM CODE",
                "Ean Codes": "ITEM CODE",
                "Style Code": "ITEM NAME",
                "Price": "MRP",
                "Color": "COLOR",
                "brand  size": "SIZE",
                "Country of Origin": "IMPORTED/DOMESTIC",
                "HSN Codes": "HS CODE",
                "Product Name": "Nykaa Title",
                "Description": "Description",
                "Gender": "GENDER",
                "Design Code": "Design Code Group"
            }
        )
        self.db.add(config)
        
        # 3. Create mock size charts
        size_3_4 = SizeMapping(
            category_name="Tshirts",
            brand_size="3-4Y",
            measurements={
                "Chest for Garment (Inches)": 36,
                "Waist for Garment (Inches)": 27,
                "Shoulder for Garment (Inches)": 29,
                "Length (Inches)": 61
            }
        )
        self.db.add(size_3_4)
        self.db.commit()
        
        # 4. Create Generation Job for Style code: PBPOHS002797-RED
        job = ProcessingJob(
            job_id="test_run_123",
            status="running",
            progress=10,
            category="Tshirts",
            input_codes="PBPOHS002797-RED"
        )
        self.db.add(job)
        self.db.commit()
        
        # Run generator
        generate_nykaa_template(self.db, job, logger)
        
        # Assert generation job completed successfully
        self.assertEqual(job.status, "success")
        self.assertEqual(job.progress, 100)
        self.assertIsNotNone(job.output_file_id)
        
        # Verify the generated file is in database
        out_file = self.db.query(DbFile).filter(DbFile.id == job.output_file_id).first()
        self.assertIsNotNone(out_file)
        self.assertTrue(out_file.filename.startswith("Nykaa_Populated_"))

    def test_03_prune_excel_file(self):
        # 1. Test Content Sheet Pruning
        content_path = "Raw Files/Content Sheet.xlsx"
        self.assertTrue(os.path.exists(content_path))
        with open(content_path, "rb") as f:
            orig_bytes = f.read()
        
        pruned_bytes = database.prune_excel_file(orig_bytes, "content_sheet")
        self.assertLess(len(pruned_bytes), len(orig_bytes))
        
        import io
        import pandas as pd
        df = pd.read_excel(io.BytesIO(pruned_bytes))
        # Ensure only the 5 required columns are present
        expected_cols = ['Item Name', 'SHADE NAME', 'Nykaa Title', 'Description', 'Product Image']
        self.assertEqual(sorted(df.columns.tolist()), sorted(expected_cols))
        
        # 2. Test Item Directory Pruning
        dir_path = "Raw Files/ITEM DIRECTORY Main.xlsx"
        self.assertTrue(os.path.exists(dir_path))
        with open(dir_path, "rb") as f:
            orig_dir_bytes = f.read()
            
        pruned_dir_bytes = database.prune_excel_file(orig_dir_bytes, "item_directory")
        self.assertLess(len(pruned_dir_bytes), len(orig_dir_bytes))
        
        df_dir = pd.read_excel(io.BytesIO(pruned_dir_bytes))
        expected_dir_cols = [
            'ITEM NAME', 'COLOR', 'Item Color', 'CATEGORY', 'SUB CATEGORY', 
            'SIZE', 'ITEM CODE', 'MRP', 'HS CODE', 'GENDER', 'MATERIAL', 
            'FABRIC', 'IMPORTED/DOMESTIC', 'Brand', 'LENGTH IN CM'
        ]
        self.assertEqual(sorted(df_dir.columns.tolist()), sorted(expected_dir_cols))

if __name__ == '__main__':
    unittest.main()
