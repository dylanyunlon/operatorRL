"""
Agent Tools

Standard tools for the Carbon Auditor system with provenance metadata support.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import hashlib
import json


@dataclass
class ProvenanceMetadata:
    """
    Cryptographic provenance for tool outputs.
    
    This enables verification that data hasn't been tampered with
    and tracks the original source.
    """
    signature: str  # SHA-256 hash of the data
    source: str  # Original data source (e.g., "copernicus.eu")
    timestamp: datetime
    algorithm: str = "sha256"
    
    @classmethod
    def create(cls, data: Dict[str, Any], source: str) -> "ProvenanceMetadata":
        """Create provenance metadata for data."""
        # Serialize deterministically
        data_str = json.dumps(data, sort_keys=True, default=str)
        signature = hashlib.sha256(data_str.encode()).hexdigest()
        
        return cls(
            signature=f"sha256:{signature}",
            source=source,
            timestamp=datetime.utcnow(),
        )
    
    def verify(self, data: Dict[str, Any]) -> bool:
        """Verify data matches the provenance signature."""
        data_str = json.dumps(data, sort_keys=True, default=str)
        expected = hashlib.sha256(data_str.encode()).hexdigest()
        return self.signature == f"sha256:{expected}"


@dataclass
class ToolResult:
    """
    Result from a tool execution.
    
    Includes both the data and provenance metadata for verification.
    """
    success: bool
    data: Dict[str, Any]
    provenance: Optional[ProvenanceMetadata] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        result = {
            "success": self.success,
            "data": self.data,
            "error": self.error,
        }
        if self.provenance:
            result["provenance"] = {
                "signature": self.provenance.signature,
                "source": self.provenance.source,
                "timestamp": self.provenance.timestamp.isoformat(),
                "algorithm": self.provenance.algorithm,
            }
        return result


class Tool(ABC):
    """
    Base class for all agent tools.
    
    All tools must provide:
    - A unique name
    - A list of capabilities
    - An execute method
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> List[str]:
        """List of capability tags for discovery."""
        pass

    @abstractmethod
    def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given parameters."""
        pass


class PDFParserTool(Tool):
    """
    Parses PDF documents to extract text and structure.
    
    Capabilities: pdf_parsing, text_extraction
    """

    @property
    def name(self) -> str:
        return "pdf_parser"

    @property
    def capabilities(self) -> List[str]:
        return ["pdf_parsing", "text_extraction", "document_ingestion"]

    def execute(self, pdf_path: str, **kwargs) -> ToolResult:
        """
        Parse a PDF and extract text content.
        
        Args:
            pdf_path: Path to the PDF file
            
        Returns:
            ToolResult with extracted text
        """
        try:
            # For mock implementation, we'll simulate PDF parsing
            # In production, this would use pypdf
            from pathlib import Path
            
            path = Path(pdf_path)
            if not path.exists():
                # Return mock data for demo purposes
                data = {
                    "text": self._get_mock_pdf_content(),
                    "pages": 5,
                    "filename": path.name,
                }
            else:
                # Check if it's a text file (for demo purposes)
                if path.suffix in ['.txt', '.md']:
                    with open(path, 'r', encoding='utf-8') as f:
                        text = f.read()
                    data = {
                        "text": text,
                        "pages": 1,
                        "filename": path.name,
                    }
                else:
                    # Attempt real PDF parsing
                    try:
                        from pypdf import PdfReader
                        reader = PdfReader(pdf_path)
                        text = ""
                        for page in reader.pages:
                            text += page.extract_text() + "\n"
                        data = {
                            "text": text,
                            "pages": len(reader.pages),
                            "filename": path.name,
                        }
                    except ImportError:
                        data = {
                            "text": self._get_mock_pdf_content(),
                            "pages": 5,
                            "filename": path.name,
                        }

            provenance = ProvenanceMetadata.create(data, f"file://{pdf_path}")
            
            return ToolResult(
                success=True,
                data=data,
                provenance=provenance,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=str(e),
            )

    def _get_mock_pdf_content(self) -> str:
        """Return mock PDF content for demo."""
        return """
PROJECT DESIGN DOCUMENT
Voluntary Carbon Standard (VCS)

Project ID: VCS-2024-FOREST-001
Project Name: Amazon Rainforest Conservation Initiative

1. PROJECT DESCRIPTION
This project aims to protect 10,000 hectares of primary rainforest
in the Amazon basin from planned deforestation.

2. GEOSPATIAL BOUNDARIES
Project Polygon Coordinates (WGS84):
[-62.215, -3.465], [-62.180, -3.465], [-62.180, -3.430], [-62.215, -3.430]

3. BASELINE CARBON STOCK
Year: 2024
Forest Type: Tropical Moist Forest
Carbon Stock: 180 tonnes CO2/hectare
NDVI Baseline: 0.82

4. CLAIMED EMISSION REDUCTIONS
Annual avoided deforestation: 500 hectares
Claimed carbon credits: 90,000 tCO2e/year

5. MONITORING METHODOLOGY
Sentinel-2 satellite imagery analysis
Reference Period: 2020-2024
"""


class TableExtractorTool(Tool):
    """
    Extracts structured tables from documents.
    
    Capabilities: table_extraction, data_structuring
    """

    @property
    def name(self) -> str:
        return "table_extractor"

    @property
    def capabilities(self) -> List[str]:
        return ["table_extraction", "data_structuring"]

    def execute(self, text: str, **kwargs) -> ToolResult:
        """
        Extract structured data from text.
        
        Args:
            text: The document text to parse
            
        Returns:
            ToolResult with extracted structured data
        """
        import re

        try:
            data = {
                "project_id": None,
                "polygon": None,
                "year": None,
                "claimed_ndvi": None,
                "carbon_stock": None,
            }

            # Extract Project ID
            project_match = re.search(r'Project ID:\s*(VCS-[\w-]+)', text)
            if project_match:
                data["project_id"] = project_match.group(1)

            # Extract coordinates
            coord_match = re.search(
                r'Polygon Coordinates.*?:\s*(\[[-\d.,\s\[\]]+\])',
                text, re.DOTALL
            )
            if coord_match:
                data["polygon"] = coord_match.group(1)

            # Extract year
            year_match = re.search(r'Year:\s*(\d{4})', text)
            if year_match:
                data["year"] = int(year_match.group(1))

            # Extract NDVI
            ndvi_match = re.search(r'NDVI.*?:\s*([\d.]+)', text)
            if ndvi_match:
                data["claimed_ndvi"] = float(ndvi_match.group(1))

            # Extract carbon stock
            carbon_match = re.search(r'Carbon Stock:\s*([\d.]+)', text)
            if carbon_match:
                data["carbon_stock"] = float(carbon_match.group(1))

            provenance = ProvenanceMetadata.create(data, "extraction:table_extractor")

            return ToolResult(
                success=True,
                data=data,
                provenance=provenance,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=str(e),
            )


class SentinelAPITool(Tool):
    """
    Interface to Sentinel-2 satellite imagery.
    
    Capabilities: satellite_data, imagery_fetch, sentinel_2
    
    In production, this would connect to Copernicus Open Access Hub.
    For demo purposes, returns mock data.
    """

    @property
    def name(self) -> str:
        return "sentinel_api"

    @property
    def capabilities(self) -> List[str]:
        return ["satellite_data", "imagery_fetch", "sentinel_2"]

    def execute(
        self,
        polygon: str,
        start_date: str,
        end_date: str,
        **kwargs
    ) -> ToolResult:
        """
        Fetch Sentinel-2 imagery for a polygon and date range.
        
        Args:
            polygon: GeoJSON polygon coordinates
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            
        Returns:
            ToolResult with imagery metadata and mock bands
        """
        try:
            # Mock satellite data fetch
            # In production: connect to Copernicus, download tiles
            data = {
                "product_type": "S2MSI2A",
                "tile_id": "T20MQA",
                "acquisition_date": "2024-06-15",
                "cloud_cover_percentage": 8.5,
                "bands": {
                    "B04_RED": "mock_red_band_data",
                    "B08_NIR": "mock_nir_band_data",
                },
                "spatial_resolution": 10,  # meters
                "crs": "EPSG:32720",
            }

            provenance = ProvenanceMetadata.create(
                data,
                "copernicus.eu/sentinel-2"
            )

            return ToolResult(
                success=True,
                data=data,
                provenance=provenance,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=str(e),
            )


class NDVICalculatorTool(Tool):
    """
    Calculates NDVI from satellite imagery bands.
    
    NDVI = (NIR - RED) / (NIR + RED)
    
    Values range from -1 to 1:
    - Dense vegetation: 0.6 to 0.9
    - Sparse vegetation: 0.2 to 0.5
    - Bare soil/rock: -0.1 to 0.1
    - Water: -1 to 0
    """

    @property
    def name(self) -> str:
        return "ndvi_calculator"

    @property
    def capabilities(self) -> List[str]:
        return ["vegetation_index", "ndvi", "remote_sensing"]

    def execute(
        self,
        red_band: Any,
        nir_band: Any,
        simulate_deforestation: bool = False,
        **kwargs
    ) -> ToolResult:
        """
        Calculate NDVI from RED and NIR bands.
        
        Args:
            red_band: Red band data (B04)
            nir_band: NIR band data (B08)
            simulate_deforestation: If True, return low NDVI values
            
        Returns:
            ToolResult with NDVI statistics
        """
        import numpy as np

        try:
            if simulate_deforestation:
                # Simulate deforestation scenario (fraud case)
                # Large areas of bare soil mixed with some vegetation
                np.random.seed(42)
                ndvi_values = np.random.uniform(0.15, 0.55, size=(100, 100))
                ndvi_values[20:60, 30:70] = np.random.uniform(0.05, 0.25, size=(40, 40))
            else:
                # Simulate healthy forest
                np.random.seed(42)
                ndvi_values = np.random.uniform(0.65, 0.88, size=(100, 100))

            data = {
                "ndvi_mean": float(np.mean(ndvi_values)),
                "ndvi_std": float(np.std(ndvi_values)),
                "ndvi_min": float(np.min(ndvi_values)),
                "ndvi_max": float(np.max(ndvi_values)),
                "pixel_count": int(ndvi_values.size),
                "vegetation_coverage": float(np.mean(ndvi_values > 0.4)),
                "deforestation_indicator": float(np.mean(ndvi_values < 0.3)),
            }

            provenance = ProvenanceMetadata.create(
                data,
                "calculation:ndvi_calculator"
            )

            return ToolResult(
                success=True,
                data=data,
                provenance=provenance,
            )

        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=str(e),
            )
