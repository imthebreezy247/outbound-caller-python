"""Quick test call to Max's number"""
import asyncio
from make_call import dispatch_outbound_call

if __name__ == "__main__":
    print("\n" + "="*60)
    print("Test Call to: +18155308498")
    print("="*60 + "\n")

    # Call this number
    asyncio.run(dispatch_outbound_call(
        phone_number="+18155308498",
        transfer_number="+18155308498"
    ))
