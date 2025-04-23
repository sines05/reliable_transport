import argparse
import socket
import sys
import time
import threading
from queue import Queue
import os

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import PacketHeader, compute_checksum

# Constants
PACKET_SIZE = 1472  # Maximum UDP packet size
HEADER_SIZE = 16    # Size of PacketHeader
DATA_SIZE = PACKET_SIZE - HEADER_SIZE
TIMEOUT = 0.5       # 500ms timeout
PACKET_TYPES = {
    'START': 0,
    'END': 1,
    'DATA': 2,
    'ACK': 3
}

class Sender:
    def __init__(self, receiver_ip, receiver_port, window_size):
        self.receiver_ip = receiver_ip
        self.receiver_port = receiver_port
        self.window_size = window_size
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.base = 1  # Start with seq_num 1 after START
        self.next_seq = 1  # Start with seq_num 1 after START
        self.packets = []
        self.timer = None
        self.timer_lock = threading.Lock()
        self.ack_queue = Queue()
        self.running = True

    def create_packet(self, pkt_type, seq_num, data=b''):
        """Create a packet with the given type, sequence number and data."""
        pkt_header = PacketHeader(
            type=pkt_type,
            seq_num=seq_num,
            length=len(data)
        )
        pkt = pkt_header / data
        pkt_header.checksum = compute_checksum(pkt)
        return pkt_header / data

    def send_packet(self, pkt):
        """Send a packet to the receiver."""
        pkt_type = "UNKNOWN"
        for name, value in PACKET_TYPES.items():
            if value == pkt.type:
                pkt_type = name
                break
        print(f"Sending {pkt_type} packet with seq_num={pkt.seq_num}", file=sys.stderr)
        self.socket.sendto(bytes(pkt), (self.receiver_ip, self.receiver_port))

    def start_timer(self):
        """Start or restart the retransmission timer."""
        with self.timer_lock:
            if self.timer is not None:
                self.timer.cancel()
            self.timer = threading.Timer(TIMEOUT, self.handle_timeout)
            self.timer.start()

    def handle_timeout(self):
        """Handle timeout by retransmitting all packets in the current window."""
        if not self.running:
            return
        with self.timer_lock:
            print(f"Timeout: retransmitting packets {self.base} to {min(self.base + self.window_size, len(self.packets) + 1)}", file=sys.stderr)
            # Retransmit all packets in the current window
            for i in range(self.base, min(self.base + self.window_size, len(self.packets) + 1)):
                self.send_packet(self.packets[i - 1])  # Adjust index since packets are 0-based
            self.start_timer()

    def process_ack(self, ack_pkt):
        """Process received ACK packet."""
        if ack_pkt.type != PACKET_TYPES['ACK']:
            return

        ack_seq = ack_pkt.seq_num
        print(f"Received ACK with seq_num={ack_seq}, current base={self.base}", file=sys.stderr)
        
        # Update base if ACK advances the window
        if ack_seq > self.base:
            self.base = ack_seq
            self.start_timer()  # Reset timer on progress

    def receive_acks(self):
        """Thread function to receive ACKs."""
        while self.running:
            try:
                data, _ = self.socket.recvfrom(2048)
                ack_pkt = PacketHeader(data)
                self.ack_queue.put(ack_pkt)
            except:
                continue

    def run(self):
        """Main sender function."""
        # Read input from stdin
        data = sys.stdin.buffer.read()
        print(f"Read {len(data)} bytes from stdin", file=sys.stderr)
        
        # Split data into chunks
        chunks = [data[i:i+DATA_SIZE] for i in range(0, len(data), DATA_SIZE)]
        print(f"Split into {len(chunks)} chunks", file=sys.stderr)
        
        # Create packets
        self.packets = []
        for i, chunk in enumerate(chunks):
            self.packets.append(self.create_packet(PACKET_TYPES['DATA'], i + 1, chunk))

        # Start ACK receiver thread
        ack_thread = threading.Thread(target=self.receive_acks)
        ack_thread.daemon = True
        ack_thread.start()

        # Send START packet and wait for ACK
        start_pkt = self.create_packet(PACKET_TYPES['START'], 0)
        self.send_packet(start_pkt)
        
        # Wait for START ACK
        while True:
            try:
                ack_pkt = self.ack_queue.get(timeout=TIMEOUT)
                if ack_pkt.type == PACKET_TYPES['ACK'] and ack_pkt.seq_num == 1:
                    print("Received START ACK", file=sys.stderr)
                    break
            except:
                print("Timeout waiting for START ACK, retransmitting", file=sys.stderr)
                self.send_packet(start_pkt)

        # Send data packets
        self.start_timer()
        while self.base <= len(self.packets):
            # Send new packets if window allows
            while self.next_seq < min(self.base + self.window_size, len(self.packets) + 1):
                self.send_packet(self.packets[self.next_seq - 1])  # Adjust index since packets are 0-based
                self.next_seq += 1

            # Process ACKs
            try:
                ack_pkt = self.ack_queue.get(timeout=TIMEOUT)
                self.process_ack(ack_pkt)
            except:
                pass

        # Send END packet and wait for ACK or timeout
        end_pkt = self.create_packet(PACKET_TYPES['END'], len(self.packets) + 1)
        self.send_packet(end_pkt)
        
        end_time = time.time() + TIMEOUT
        while time.time() < end_time:
            try:
                ack_pkt = self.ack_queue.get(timeout=end_time - time.time())
                if ack_pkt.type == PACKET_TYPES['ACK'] and ack_pkt.seq_num == len(self.packets) + 2:
                    print("Received END ACK", file=sys.stderr)
                    break
            except:
                continue

        # Cleanup
        self.running = False
        with self.timer_lock:
            if self.timer is not None:
                self.timer.cancel()
        self.socket.close()

def sender(receiver_ip, receiver_port, window_size):
    """Open socket and send message from sys.stdin."""
    s = Sender(receiver_ip, receiver_port, window_size)
    s.run()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "receiver_ip", help="The IP address of the host that receiver is running on"
    )
    parser.add_argument(
        "receiver_port", type=int, help="The port number on which receiver is listening"
    )
    parser.add_argument(
        "window_size", type=int, help="Maximum number of outstanding packets"
    )
    args = parser.parse_args()

    sender(args.receiver_ip, args.receiver_port, args.window_size)

if __name__ == "__main__":
    main()
