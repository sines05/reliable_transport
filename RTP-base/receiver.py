import argparse
import socket
import sys
from collections import defaultdict
import os

# Add the current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import PacketHeader, compute_checksum

# Constants
PACKET_TYPES = {
    'START': 0,
    'END': 1,
    'DATA': 2,
    'ACK': 3
}

class Receiver:
    def __init__(self, receiver_ip, receiver_port, window_size):
        self.receiver_ip = receiver_ip
        self.receiver_port = receiver_port
        self.window_size = window_size
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((receiver_ip, receiver_port))
        self.expected_seq = 1  # Start expecting seq_num 1 after START
        self.buffer = defaultdict(bytes)
        self.connection_established = False
        self.sender_address = None

    def create_ack_packet(self, seq_num):
        """Create an ACK packet with the given sequence number."""
        pkt_header = PacketHeader(
            type=PACKET_TYPES['ACK'],
            seq_num=seq_num,
            length=0
        )
        pkt = pkt_header / b''
        pkt_header.checksum = compute_checksum(pkt)
        return pkt_header / b''

    def send_ack(self, seq_num):
        """Send an ACK packet to the sender."""
        if self.sender_address is None:
            return
        print(f"Sending ACK with seq_num={seq_num}", file=sys.stderr)
        ack_pkt = self.create_ack_packet(seq_num)
        self.socket.sendto(bytes(ack_pkt), self.sender_address)

    def verify_checksum(self, pkt):
        """Verify the checksum of a received packet."""
        pkt_checksum = pkt.checksum
        pkt.checksum = 0
        computed_checksum = compute_checksum(pkt)
        pkt.checksum = pkt_checksum
        return pkt_checksum == computed_checksum

    def handle_start_packet(self, pkt, address):
        """Handle START packet and establish connection."""
        if not self.connection_established:
            print("Received START packet, establishing connection", file=sys.stderr)
            self.connection_established = True
            self.sender_address = address
            self.expected_seq = 1  # Start expecting seq_num 1 after START
            self.buffer.clear()
            self.send_ack(1)  # ACK for START has seq_num 1

    def handle_data_packet(self, pkt, address):
        """Handle DATA packet."""
        if not self.connection_established or address != self.sender_address:
            print("Ignoring DATA packet: no connection or wrong sender", file=sys.stderr)
            return

        # Verify checksum
        if not self.verify_checksum(pkt):
            print(f"Checksum verification failed for packet {pkt.seq_num}", file=sys.stderr)
            # Send ACK for last in-order packet received
            self.send_ack(self.expected_seq)
            return

        seq_num = pkt.seq_num
        data = bytes(pkt.payload)
        print(f"Received DATA packet with seq_num={seq_num}, length={len(data)}", file=sys.stderr)

        # Drop packets outside the window
        if seq_num >= self.expected_seq + self.window_size:
            print(f"Dropping packet {seq_num} outside window", file=sys.stderr)
            # Send ACK for last in-order packet received
            self.send_ack(self.expected_seq)
            return

        # Store packet in buffer if it's not already received
        if seq_num >= self.expected_seq:
            self.buffer[seq_num] = data

        # Process in-order packets
        while self.expected_seq in self.buffer:
            print(f"Processing in-order packet {self.expected_seq}", file=sys.stderr)
            sys.stdout.buffer.write(self.buffer[self.expected_seq])
            sys.stdout.flush()
            del self.buffer[self.expected_seq]
            self.expected_seq += 1

        # Send cumulative ACK for next expected packet
        self.send_ack(self.expected_seq)

    def handle_end_packet(self, pkt, address):
        """Handle END packet."""
        if not self.connection_established or address != self.sender_address:
            print("Ignoring END packet: no connection or wrong sender", file=sys.stderr)
            return

        print("Received END packet", file=sys.stderr)
        
        # If END packet is in order, send ACK with seq_num = pkt.seq_num + 1
        if pkt.seq_num == self.expected_seq:
            self.send_ack(pkt.seq_num + 1)
            # Close connection
            self.connection_established = False
            self.sender_address = None
            self.socket.close()
            sys.exit(0)
        else:
            # If END packet is out of order, send ACK for next expected packet
            self.send_ack(self.expected_seq)

    def run(self):
        """Main receiver function."""
        print(f"Receiver listening on {self.receiver_ip}:{self.receiver_port}", file=sys.stderr)
        while True:
            try:
                data, address = self.socket.recvfrom(2048)
                pkt = PacketHeader(data)
                pkt_type = "UNKNOWN"
                for name, value in PACKET_TYPES.items():
                    if value == pkt.type:
                        pkt_type = name
                        break
                print(f"Received {pkt_type} packet with seq_num={pkt.seq_num}", file=sys.stderr)

                if pkt.type == PACKET_TYPES['START']:
                    self.handle_start_packet(pkt, address)
                elif pkt.type == PACKET_TYPES['DATA']:
                    self.handle_data_packet(pkt, address)
                elif pkt.type == PACKET_TYPES['END']:
                    self.handle_end_packet(pkt, address)
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                continue

def receiver(receiver_ip, receiver_port, window_size):
    """Listen on socket and print received message to sys.stdout."""
    r = Receiver(receiver_ip, receiver_port, window_size)
    r.run()

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

    receiver(args.receiver_ip, args.receiver_port, args.window_size)

if __name__ == "__main__":
    main()
