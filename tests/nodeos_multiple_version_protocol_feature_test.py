#!/usr/bin/env python3

from testUtils import Utils
from Cluster import Cluster, PFSetupPolicy
from TestHelper import TestHelper
from WalletMgr import WalletMgr
from Node import Node

import signal
import json
import time
from os.path import join
from datetime import datetime

# Parse command line arguments
args = TestHelper.parse_args({"-v","--clean-run","--dump-error-details","--leave-running",
                              "--keep-logs", "--alternate-version-labels-file"})
Utils.Debug=args.v
killAll=args.clean_run
dumpErrorDetails=args.dump_error_details
dontKill=args.leave_running
killEosInstances=not dontKill
killWallet=not dontKill
keepLogs=args.keep_logs
alternateVersionLabelsFile=args.alternate_version_labels_file

walletMgr=WalletMgr(True)
cluster=Cluster(walletd=True)
cluster.setWalletMgr(walletMgr)

def restartNode(node: Node, nodeId, chainArg=None, addOrSwapFlags=None, nodeosPath=None):
    if not node.killed:
        node.kill(signal.SIGTERM)
    isRelaunchSuccess = node.relaunch(nodeId, chainArg, addOrSwapFlags=addOrSwapFlags,
                                      timeout=5, cachePopen=True, nodeosPath=nodeosPath)
    assert isRelaunchSuccess, "Fail to relaunch"

# List to contain the test result message
testSuccessful = False
try:
    TestHelper.printSystemInfo("BEGIN")
    cluster.killall(allInstances=killAll)
    cluster.cleanup()
    if not alternateVersionLabelsFile:
        alternateVersionLabelsFile="/Users/andrianto/alternate-version-labels-file.txt"
    associatedNodeLabels = {
        "3": "170"
    }
    cluster.launch(pnodes=4, totalNodes=4, prodCount=1, totalProducers=4,
                   extraNodeosArgs=" --plugin eosio::producer_api_plugin ",
                   useBiosBootFile=False,
                   onlySetProds=True,
                   pfSetupPolicy=PFSetupPolicy.NONE,
                   alternateVersionLabelsFile=alternateVersionLabelsFile,
                   associatedNodeLabels=associatedNodeLabels)

    def pauseBlockProduction():
        for node in cluster.nodes:
            node.sendRpcApi("v1/producer/pause")

    def resumeBlockProduction():
        for node in cluster.nodes:
            node.sendRpcApi("v1/producer/resume")

    def checkIfNodesAreInSync(nodes:[Node]):
        # Pause all block production, so the head is not moving
        pauseBlockProduction()
        time.sleep(1) # Wait for some time to ensure, all blocks are propagated
        headBlockIds = []
        for node in nodes:
            headBlockId = node.getInfo()["head_block_id"]
            headBlockIds.append(headBlockId)
        # Resume all block production
        resumeBlockProduction()
        return len(set(headBlockIds)) == 1

    def ensureNodeContainPreactivateFeature(node):
        preactivateFeatureDigest = node.getSupportedProtocolFeatureDict()["PREACTIVATE_FEATURE"]["feature_digest"]
        assert preactivateFeatureDigest
        blockHeaderState = node.getLatestBlockHeaderState()
        activatedProtocolFeatures = blockHeaderState["activated_protocol_features"]["protocol_features"]
        print(preactivateFeatureDigest, blockHeaderState)
        assert preactivateFeatureDigest in activatedProtocolFeatures

    newVersionNodeIds = [0, 1, 2]
    oldVersionNodeId = 3
    newVersionNodes = list(map(lambda id: cluster.getNode(id), newVersionNodeIds))
    oldVersionNode = cluster.getNode(oldVersionNodeId)
    allNodes = [*newVersionNodes, oldVersionNode]

    # Ensure everything is in sync
    assert checkIfNodesAreInSync(allNodes), "Nodes are not in sync before preactivation"

    newVersionNodes[0].activatePreactivateFeature()
    # LIB should still advance on node with new versions, but not on the old one
    assert newVersionNodes[0].waitForLibToAdvance()
    newVersionNode0Lib = newVersionNodes[0].getIrreversibleBlockNum()
    assert newVersionNodes[1].getIrreversibleBlockNum() >= newVersionNode0Lib and newVersionNodes[1].getIrreversibleBlockNum() >= newVersionNode0Lib
    # assert not oldVersionNode.waitForLibToAdvance()

    # Old version node should be out of sync, but new version nodes are in sync
    assert not checkIfNodesAreInSync(allNodes), "Nodes should not be in sync after preactivation"
    assert checkIfNodesAreInSync(newVersionNodes), "New nodes should be in sync after preactivation"
    # And PREACTIVATE_FEATURE should be activated on all the new version nodes
    for node in newVersionNodes: ensureNodeContainPreactivateFeature(node)

    # Restart old node with newest version
    restartNode(oldVersionNode, oldVersionNodeId, chainArg=" --replay ", nodeosPath="programs/nodeos/nodeos")
    time.sleep(2) # Give some time to replay

    # assert checkIfNodesAreInSync(allNodes), "Nodes are not in sync after old version node is restarted with as new version"
    ensureNodeContainPreactivateFeature(oldVersionNode)

    testSuccessful = True
finally:
    TestHelper.shutdown(cluster, walletMgr, testSuccessful, killEosInstances, killWallet, keepLogs, killAll, dumpErrorDetails)

exitCode = 0 if testSuccessful else 1
exit(exitCode)
