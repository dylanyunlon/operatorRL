"""
Claims Agent (Agent A) - "The Reader"

Ingests Project Design Documents (PDFs) and extracts carbon credit claims.
Publishes structured Claim objects to the message bus.
"""

from typing import Any, Dict, List, Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amb import Message, MessageType
from amb.topics import Topics
from atr import PDFParserTool, TableExtractorTool, ToolRegistry
from .base import Agent, MessageBus


class ClaimsAgent(Agent):
    """
    The Claims Agent - "The Reader"
    
    Role: Ingests Project Design Documents (PDFs) and extracts:
        - Project identification
        - Geospatial polygon coordinates
        - Claimed carbon stock values
        - Claimed NDVI/vegetation values
    
    Tooling: pdf_parser, table_extractor
    
    Publishes: Claim objects to the CLAIMS topic
    """

    def __init__(self, agent_id: str, bus: MessageBus):
        super().__init__(agent_id, bus, name="claims-agent")
        
        # Initialize tools
        self._pdf_parser = PDFParserTool()
        self._table_extractor = TableExtractorTool()
        
        # Register tools
        self._registry = ToolRegistry()
        self._registry.register(self._pdf_parser)
        self._registry.register(self._table_extractor)

    @property
    def subscribed_topics(self) -> List[str]:
        """Claims agent doesn't subscribe to any topics - it initiates the flow."""
        return [Topics.SYSTEM]

    def handle_message(self, message: Message) -> None:
        """Handle system messages (e.g., requests to process a document)."""
        if message.payload.get("command") == "process_document":
            pdf_path = message.payload.get("pdf_path")
            if pdf_path:
                self.process_document(pdf_path, message.correlation_id)

    def process_document(
        self,
        pdf_path: str,
        correlation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a Project Design Document and extract claims.
        
        Args:
            pdf_path: Path to the PDF file
            correlation_id: Optional ID for tracking the request
            
        Returns:
            The extracted claim data
        """
        self._log(f"Processing document: {pdf_path}")
        
        # Step 1: Parse PDF
        parse_result = self._pdf_parser.execute(pdf_path=pdf_path)
        
        if not parse_result.success:
            self._log(f"Failed to parse PDF: {parse_result.error}", level="ERROR")
            return {"error": parse_result.error}
        
        self._log(f"Parsed {parse_result.data['pages']} pages")
        
        # Step 2: Extract structured data
        extract_result = self._table_extractor.execute(
            text=parse_result.data["text"]
        )
        
        if not extract_result.success:
            self._log(f"Failed to extract data: {extract_result.error}", level="ERROR")
            return {"error": extract_result.error}
        
        # Step 3: Build claim object
        claim = self._build_claim(extract_result.data)
        
        self._log(f"Extracted claim: {claim['project_id']}, NDVI={claim['claimed_ndvi']}")
        
        # Step 4: Publish to bus
        self.publish(
            topic=Topics.CLAIMS,
            payload=claim,
            message_type=MessageType.CLAIM,
            correlation_id=correlation_id,
        )
        
        # Also include provenance
        if extract_result.provenance:
            claim["_provenance"] = {
                "signature": extract_result.provenance.signature,
                "source": extract_result.provenance.source,
            }
        
        return claim

    def _build_claim(self, extracted_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a standardized claim object from extracted data.
        """
        return {
            "project_id": extracted_data.get("project_id", "UNKNOWN"),
            "polygon": extracted_data.get("polygon"),
            "year": extracted_data.get("year", 2024),
            "claimed_ndvi": extracted_data.get("claimed_ndvi", 0.0),
            "claimed_carbon_stock": extracted_data.get("carbon_stock", 0.0),
        }

    def get_tools(self) -> List[str]:
        """List available tools."""
        return self._registry.list_tools()
