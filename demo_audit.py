#!/usr/bin/env python3
"""
Carbon Auditor Swarm - Demo Script

Demonstrates the autonomous auditing system for the Voluntary Carbon Market.

This script:
1. Spins up three agents (claims-agent, geo-agent, auditor-agent)
2. Processes a mock Project Design Document
3. Simulates satellite data showing deforestation
4. Uses cmvk (Verification Kernel) to detect fraud
5. Prints the FRAUD alert to console

"The AI didn't decide; the Math decided. The AI just managed the workflow."
"""

import sys
import time
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from amb import MessageBus, Message, MessageType
from amb.topics import Topics
from agents import ClaimsAgent, GeoAgent, AuditorAgent


def print_banner():
    """Print the demo banner."""
    print("\n" + "="*70)
    print("  CARBON AUDITOR SWARM - Voluntary Carbon Market Verification")
    print("="*70)
    print("  'The AI didn't decide; the Math decided.'")
    print("="*70 + "\n")


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'─'*70}")
    print(f"  {title}")
    print(f"{'─'*70}\n")


def run_demo(simulate_fraud: bool = True):
    """
    Run the Carbon Auditor demo.
    
    Args:
        simulate_fraud: If True, simulate deforestation (fraud case)
                       If False, simulate healthy forest (verified case)
    """
    print_banner()
    
    # ─────────────────────────────────────────────────────────────────────────
    # STEP 1: Initialize the Message Bus
    # ─────────────────────────────────────────────────────────────────────────
    print_section("STEP 1: Initializing Message Bus (AMB)")
    
    bus = MessageBus()
    print("[✓] Message bus initialized")
    print(f"    Available topics: {[Topics.CLAIMS, Topics.OBSERVATIONS, Topics.VERIFICATION_RESULTS, Topics.ALERTS]}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # STEP 2: Initialize the Agents
    # ─────────────────────────────────────────────────────────────────────────
    print_section("STEP 2: Initializing Agent Swarm")
    
    # Agent A: Claims Agent (The Reader)
    claims_agent = ClaimsAgent(
        agent_id="claims-agent-001",
        bus=bus,
    )
    print(f"[✓] {claims_agent.name} initialized")
    print(f"    Tools: {claims_agent.get_tools()}")
    
    # Agent B: Geo Agent (The Eye)
    geo_agent = GeoAgent(
        agent_id="geo-agent-001",
        bus=bus,
        simulate_deforestation=simulate_fraud,  # This controls the demo scenario
    )
    print(f"[✓] {geo_agent.name} initialized")
    print(f"    Tools: {geo_agent.get_tools()}")
    print(f"    Simulation mode: {'DEFORESTATION (Fraud)' if simulate_fraud else 'HEALTHY FOREST (Verified)'}")
    
    # Agent C: Auditor Agent (The Judge)
    auditor_agent = AuditorAgent(
        agent_id="auditor-agent-001",
        bus=bus,
        threshold=0.15,  # Drift score threshold for fraud detection
    )
    print(f"[✓] {auditor_agent.name} initialized")
    print(f"    Verification threshold: 0.15")
    
    # ─────────────────────────────────────────────────────────────────────────
    # STEP 3: Start the Agents
    # ─────────────────────────────────────────────────────────────────────────
    print_section("STEP 3: Starting Agent Swarm")
    
    # Start all agents (they subscribe to their topics)
    claims_agent.start()
    geo_agent.start()
    auditor_agent.start()
    
    print("[✓] All agents started and subscribed to topics")
    time.sleep(0.1)  # Small delay for subscriptions to register
    
    # ─────────────────────────────────────────────────────────────────────────
    # STEP 4: Process the Project Design Document
    # ─────────────────────────────────────────────────────────────────────────
    print_section("STEP 4: Processing Project Design Document")
    
    # The claims agent extracts data from the PDF
    mock_pdf_path = str(Path(__file__).parent / "tests" / "data" / "project_design.txt")
    print(f"[→] Processing document: {mock_pdf_path}")
    
    claim = claims_agent.process_document(mock_pdf_path, correlation_id="audit-001")
    
    print(f"\n[✓] Claim Extracted:")
    print(f"    Project ID: {claim.get('project_id')}")
    print(f"    Year: {claim.get('year')}")
    print(f"    Claimed NDVI: {claim.get('claimed_ndvi')}")
    print(f"    Claimed Carbon Stock: {claim.get('claimed_carbon_stock')} tonnes/ha")
    
    # ─────────────────────────────────────────────────────────────────────────
    # STEP 5: Wait for Agent Pipeline to Complete
    # ─────────────────────────────────────────────────────────────────────────
    print_section("STEP 5: Agent Pipeline Processing")
    
    # Give the agents time to process
    # In production, this would be event-driven
    print("[...] Waiting for geo-agent to fetch satellite data...")
    time.sleep(0.5)
    
    print("[...] Waiting for auditor-agent to perform verification...")
    time.sleep(0.5)
    
    # ─────────────────────────────────────────────────────────────────────────
    # STEP 6: Display Results
    # ─────────────────────────────────────────────────────────────────────────
    print_section("STEP 6: Verification Results (CMVK Output)")
    
    results = auditor_agent.get_results()
    
    if results:
        result = results[-1]  # Get the most recent result
        
        print(f"Project ID: {result['project_id']}")
        print(f"Status: {result['status']}")
        print(f"Drift Score: {result['drift_score']:.4f}")
        print(f"Threshold: {result['threshold']}")
        print(f"Confidence: {result['confidence']:.2%}")
        print(f"Metric: {result['metric']}")
        
        print(f"\n--- Detailed Analysis ---")
        details = result['details']
        print(f"NDVI Claimed: {details['ndvi_claimed']}")
        print(f"NDVI Observed: {details['ndvi_observed']:.3f}")
        print(f"NDVI Discrepancy: {details['ndvi_percent_difference']:.1f}%")
        print(f"Carbon Claimed: {details['carbon_claimed']} tonnes/ha")
        print(f"Carbon Observed: {details['carbon_observed']:.1f} tonnes/ha")
        print(f"Carbon Discrepancy: {details['carbon_percent_difference']:.1f}%")
        print(f"Deforestation Indicator: {details['deforestation_indicator']:.1%}")
        
        print(f"\n--- Audit Note ---")
        print(details['audit_note'])
        
        # ─────────────────────────────────────────────────────────────────────
        # FRAUD ALERT DISPLAY
        # ─────────────────────────────────────────────────────────────────────
        if result['status'] == "FRAUD":
            print("\n")
            print("!"*70)
            print("!!" + " "*26 + "FRAUD DETECTED" + " "*26 + "!!")
            print("!"*70)
            print(f"!!")
            print(f"!!  Project: {result['project_id']}")
            print(f"!!  Drift Score: {result['drift_score']:.4f} (threshold: {result['threshold']})")
            print(f"!!")
            print(f"!!  The MATH has spoken:")
            print(f"!!  - Claimed NDVI (0.82) vs Observed NDVI ({details['ndvi_observed']:.2f})")
            print(f"!!  - This represents a {details['ndvi_percent_difference']:.0f}% discrepancy")
            print(f"!!")
            print(f"!!  RECOMMENDATION: Suspend credit issuance pending investigation")
            print(f"!!")
            print("!"*70)
            print("\n")
        
        elif result['status'] == "VERIFIED":
            print("\n")
            print("✓"*70)
            print("✓✓" + " "*26 + "VERIFIED" + " "*28 + "✓✓")
            print("✓"*70)
            print(f"  Project claims align with satellite observations.")
            print(f"  Carbon credits may be issued.")
            print("✓"*70)
            print("\n")
    
    else:
        print("[!] No verification results available")
    
    # ─────────────────────────────────────────────────────────────────────────
    # STEP 7: Kernel Statistics
    # ─────────────────────────────────────────────────────────────────────────
    print_section("STEP 7: Kernel Statistics")
    
    stats = auditor_agent.get_kernel_stats()
    print(f"Total Verifications: {stats['verification_count']}")
    print(f"Fraud Threshold: {stats['threshold']}")
    print(f"Flag Threshold: {stats['flag_threshold']}")
    
    # ─────────────────────────────────────────────────────────────────────────
    # Cleanup
    # ─────────────────────────────────────────────────────────────────────────
    print_section("Cleanup")
    
    claims_agent.stop()
    geo_agent.stop()
    auditor_agent.stop()
    
    print("[✓] All agents stopped")
    print("\n" + "="*70)
    print("  Demo Complete")
    print("="*70 + "\n")


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Carbon Auditor Swarm Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python demo_audit.py              # Run fraud detection demo (default)
  python demo_audit.py --verified   # Run verification success demo
  python demo_audit.py --both       # Run both scenarios
        """
    )
    
    parser.add_argument(
        "--verified",
        action="store_true",
        help="Simulate healthy forest (verified scenario)"
    )
    
    parser.add_argument(
        "--both",
        action="store_true",
        help="Run both fraud and verified scenarios"
    )
    
    args = parser.parse_args()
    
    if args.both:
        print("\n" + "█"*70)
        print("█" + " "*24 + "SCENARIO 1: FRAUD" + " "*25 + "█")
        print("█"*70)
        run_demo(simulate_fraud=True)
        
        print("\n" + "█"*70)
        print("█" + " "*22 + "SCENARIO 2: VERIFIED" + " "*24 + "█")
        print("█"*70)
        run_demo(simulate_fraud=False)
    else:
        run_demo(simulate_fraud=not args.verified)


if __name__ == "__main__":
    main()
