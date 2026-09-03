// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

contract VerificationRegistry {
    struct Record {
        address recorder;
        uint256 timestamp;
        bool exists;
    }

    mapping(bytes32 => Record) private records;

    event VerificationRecorded(
        bytes32 indexed verificationHash,
        address indexed recorder,
        uint256 timestamp
    );

    function recordVerification(bytes32 verificationHash) external returns (bool) {
        require(!records[verificationHash].exists, "Hash already recorded");

        records[verificationHash] = Record({
            recorder: msg.sender,
            timestamp: block.timestamp,
            exists: true
        });

        emit VerificationRecorded(verificationHash, msg.sender, block.timestamp);
        return true;
    }

    function verificationExists(bytes32 verificationHash) external view returns (bool) {
        return records[verificationHash].exists;
    }

    function getRecord(bytes32 verificationHash) external view returns (address recorder, uint256 timestamp, bool exists) {
        Record storage record = records[verificationHash];
        return (record.recorder, record.timestamp, record.exists);
    }
}
